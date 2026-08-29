#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oc_run.py — 把编码任务外包给本机 opencode CLI 的安全封装。

能力：
  * 沙箱目录校验（黑名单，防止在 $HOME / Desktop / 系统目录裸奔）
  * 执行前快照（git HEAD / 非 git 目录文件指纹），执行后自动汇总 files_changed
  * 会话复用（按 title + dir 查回 session id，续跑保上下文）
  * ndjson 事件流解析 → 结构化 JSON（text / tools / tokens / cost）
  * 后台长任务（--bg）+ 状态查询（--status）

用法见 SKILL.md 第四节。所有输出为单个 JSON 对象（stdout）。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

HOME = Path.home()
RUNS_DIR = HOME / ".workbuddy" / ".cache" / "opencode-delegate" / "runs"
# 默认模型沿革（免费池按周滚动，务必定期跑 scripts/oc_probe.sh 复核）：
#   2026-08-10 deepseek-v4-flash-free  → 08-13 静默挂死（100s/240s 零输出），弃用
#   2026-08-13 laguna-s-2.1-free       → 08-29 已下架（UnknownError / server error），弃用
#   2026-08-29 nemotron-3.5-lightning-free ← 当前默认（9.7s，08-18 有全天实战记录，写文件通过）
DEFAULT_MODEL = "opencode/nemotron-3.5-lightning-free"
DEFAULT_TIMEOUT = 900

# 挂死/下架黑名单：显式 -m 指定这些会被自动替换为 DEFAULT_MODEL（除非加 --force-model）。
# 复活验证方式：scripts/oc_probe.sh 或单发 PONG 探活，通了就从这里移除。
# 2026-08-29 复测更新：hy3-free 已复活（4.9s 通过 + 写文件成功）→ 从黑名单移出；
#                     laguna-s-2.1-free 已从模型列表消失 → 新增；
#                     muse-spark-1.2-contributor-free 报地区不可用 → 新增。
DEAD_MODELS = {
    "opencode/deepseek-v4-flash-free",            # 2026-08-13 挂死：100s / 240s 零输出
    "opencode/laguna-s-2.1-free",                 # 2026-08-29 已下架（server error）
    "opencode/muse-spark-1.2-contributor-free",   # 2026-08-29 地区限制：not available in your country
    "opencode/longcat-2.0-free",                  # 已下架（0.8s 快速失败）
    "opencode/ling-3.0-tiny-free",                # 已下架
    "opencode/ling-3.0-flash-free",               # 已下架（注意：与在用的 ling-3.0-flash-fin-free 不是同一个）
    "opencode/north-mini-code-free",              # 已下架
}

# --- 目录安全策略 ---------------------------------------------------------
# 硬拒绝：这些目录本身（及其下的直接运行）风险过高
HARD_DENY_EXACT = {
    HOME, Path("/"), Path("/Users"), Path("/Applications"), Path("/System"),
    Path("/Library"), Path("/etc"), Path("/usr"), Path("/var"), Path("/bin"),
    Path("/sbin"), Path("/opt"), Path("/private"),
    HOME / "Desktop", HOME / "Downloads", HOME / "Documents",
    HOME / "Movies", HOME / "Music", HOME / "Pictures", HOME / "Public",
}
# 硬拒绝：这些前缀下的任何位置
HARD_DENY_PREFIX = [
    HOME / "Library", HOME / ".ssh", HOME / ".aws", HOME / ".gnupg",
    HOME / ".config", HOME / ".workbuddy", HOME / ".local", HOME / ".wb-signin",
    HOME / "ChromeDebugProfile",
    Path("/System"), Path("/Library"), Path("/etc"), Path("/usr"),
    Path("/bin"), Path("/sbin"),
]
# 警告（允许但提示）：个人目录下的子项目
WARN_PREFIX = [HOME / "Desktop", HOME / "Downloads", HOME / "Documents"]

SNAPSHOT_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist",
    "build", "target", ".idea", ".vscode", ".DS_Store", ".pytest_cache",
    ".mypy_cache", "vendor", ".gradle", "Pods",
}
SNAPSHOT_MAX_FILES = 20000


def die(msg, **extra):
    out = {"ok": False, "error": msg}
    out.update(extra)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(1)


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def opencode_bin():
    return shutil.which("opencode") or "/opt/homebrew/bin/opencode"


# --- 目录校验 -------------------------------------------------------------
def check_dir(raw, allow_unsafe=False):
    if not raw:
        die("必须指定 --dir（沙箱边界）")
    d = Path(raw).expanduser()
    if not d.exists():
        die(f"目录不存在: {d}")
    if not d.is_dir():
        die(f"不是目录: {d}")
    d = d.resolve()
    warnings = []

    def blocked(reason):
        if allow_unsafe:
            warnings.append(f"⚠️ 已用 --allow-unsafe-dir 强行放行高危目录（{reason}）——必须确认用户已明确同意")
            return
        die(f"拒绝在此目录运行 opencode（{reason}）。"
            f"请指定具体项目目录；确需突破加 --allow-unsafe-dir 且必须先征得用户明确同意。",
            dir=str(d))

    if d in HARD_DENY_EXACT:
        blocked("敏感/顶层目录")
    for p in HARD_DENY_PREFIX:
        try:
            if d == p or p in d.parents:
                blocked(f"位于受保护路径 {p}")
                break
        except Exception:
            pass
    for p in WARN_PREFIX:
        if p in d.parents:
            warnings.append(f"⚠️ 工作目录位于个人目录 {p} 下，opencode 可在其中读写与执行命令——务必确认目录内容可被改动，并在跑完审查 diff")
    return d, warnings


# --- 快照 -----------------------------------------------------------------
def is_git_repo(d):
    r = subprocess.run(["git", "-C", str(d), "rev-parse", "--is-inside-work-tree"],
                       capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


def porcelain_path(line):
    """从 `XY path` / `R  old -> new` 里取出路径"""
    p = line[3:].strip() if len(line) > 3 else line.strip()
    if " -> " in p:
        p = p.split(" -> ", 1)[1]
    return p.strip().strip('"')


def file_hash(p):
    try:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def git_state(d):
    def sh(*a):
        return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True).stdout.strip()
    dirty = sh("status", "--porcelain")
    # 已经 dirty/untracked 的文件，status 行不会再变；额外记内容 hash 才能发现它们又被改了
    dirty_hashes = {}
    for line in dirty.splitlines():
        rel = porcelain_path(line)
        fp = d / rel
        if fp.is_file():
            dirty_hashes[rel] = file_hash(fp)
    return {"head": sh("rev-parse", "HEAD"), "branch": sh("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": dirty, "dirty_hashes": dirty_hashes}


def fs_fingerprint(d):
    files = {}
    count = 0
    for root, dirs, names in os.walk(d):
        dirs[:] = [x for x in dirs if x not in SNAPSHOT_SKIP_DIRS]
        for n in names:
            p = Path(root) / n
            try:
                st = p.stat()
            except OSError:
                continue
            files[str(p.relative_to(d))] = [st.st_size, int(st.st_mtime)]
            count += 1
            if count >= SNAPSHOT_MAX_FILES:
                return files, True
    return files, False


def take_snapshot(d, enabled=True):
    if not enabled:
        return {"type": "none"}
    if is_git_repo(d):
        s = git_state(d)
        s["type"] = "git"
        return s
    files, truncated = fs_fingerprint(d)
    return {"type": "fs", "files": files, "truncated": truncated,
            "note": "非 git 仓库：无法用 git 回滚，改动风险自负；建议只在 git 仓库内外包"}


def diff_snapshot(d, snap):
    """返回 (files_changed, extra_info)"""
    if snap.get("type") == "git":
        r = subprocess.run(["git", "-C", str(d), "status", "--porcelain"],
                           capture_output=True, text=True)
        now = r.stdout.strip()
        before = set(snap.get("dirty", "").splitlines())
        changed = [l.strip() for l in now.splitlines() if l not in before]
        # 补检：跑之前就已 dirty/untracked 的文件，内容是否又变了（status 行不变，容易漏报）
        seen = {porcelain_path(c) for c in changed}
        for rel, old_hash in (snap.get("dirty_hashes") or {}).items():
            if rel in seen:
                continue
            fp = d / rel
            new_hash = file_hash(fp) if fp.is_file() else None
            if new_hash != old_hash:
                changed.append(f"M  {rel}" if new_hash else f"D  {rel}")
        head_now = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
        info = {}
        if head_now != snap.get("head"):
            info["warning"] = (f"⚠️ HEAD 变了（{snap.get('head','')[:8]} → {head_now[:8]}）："
                               f"opencode 自己提交了 commit！检查 git log，必要时 git reset --soft HEAD~1")
        return [c.strip() for c in changed], info
    if snap.get("type") == "fs":
        before = snap.get("files", {})
        after, _ = fs_fingerprint(d)
        changed = []
        for k, v in after.items():
            if k not in before:
                changed.append(f"A  {k}")
            elif before[k] != v:
                changed.append(f"M  {k}")
        for k in before:
            if k not in after:
                changed.append(f"D  {k}")
        return sorted(changed), {}
    return [], {}


# --- 会话 -----------------------------------------------------------------
def list_sessions(d, limit=50):
    r = subprocess.run([opencode_bin(), "session", "list", "--format", "json", "-n", str(limit)],
                       cwd=str(d), capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
    except Exception:
        return []
    out = []
    for s in data if isinstance(data, list) else []:
        out.append({"id": s.get("id"), "title": s.get("title"), "directory": s.get("directory")})
    return out


def same_dir(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except Exception:
        return str(a) == str(b)


def find_session(d, title, limit=80):
    for s in list_sessions(d, limit):
        if s.get("title") == title and s.get("directory") and same_dir(s["directory"], d):
            return s["id"]
    return None


# --- 额度 / 权限 识别 ------------------------------------------------------
# opencode zen 免费模型按公网 IP 限流，打满时的典型特征
QUOTA_PATTERNS = [
    "rate limit", "ratelimit", "rate_limit", "429", "too many requests",
    "quota", "exceeded", "insufficient", "out of credits", "no credits",
    "usage limit", "限流", "额度",
]
# headless 下权限请求会被自动拒绝，这行是**非 JSON** 混在 stdout 里的
PERM_LINE_RE = re.compile(r"permission requested:\s*([\w_]+)\s*\(([^)]*)\)")


def looks_like_quota(text):
    low = (text or "").lower()
    return any(k in low for k in QUOTA_PATTERNS)


def detect_quota(parsed, stderr_tail):
    """综合判断：文本 / type=error 事件 / 失败工具的错误信息里有没有限流或额度特征。"""
    blob = ((parsed.get("text") or "") + (stderr_tail or "")
            + " ".join(str(e.get("message", "")) for e in parsed.get("errors", []))
            + " ".join(str(t.get("error", "")) for t in parsed.get("failed_tools", []))).lower()
    return any(k in blob for k in QUOTA_PATTERNS)


# 失败（额度打满 / 静默挂死 / 超时）时的免费模型轮换顺序。
# 2026-08-29 串行实测重排（opencode v1.18.14，耗时为「Reply PONG」往返 + 写文件验证）：
#   ⚠️ 教训：并行探活会互相抢连接池，7 个模型一起跑时 6 个假性 STALL。必须串行测。
FREE_RETRY_ORDER = [
    "opencode/nemotron-3.5-lightning-free",  # ✅ 9.7s，写文件通过；08-18 有全天实战稳定记录 → 默认首选
    "opencode/ling-3.0-flash-fin-free",      # ✅ 6.3s，本轮最快的新模型，写文件通过
    "opencode/hy3-free",                     # ✅ 4.9s，2026-08-13 曾挂死、08-29 复活，写文件通过
    "opencode/mimo-v2.5-free",               # ✅ 10.5s，免费池唯一多模态（看图仍走它）
    "opencode/nemotron-3-ultra-free",        # ✅ 31.1s，大模型，复杂推理，慢但稳
    "opencode/big-pickle",                   # ✅ 60s 级，⚠️ 未标 free → 兜底最后一位
]
QUOTA_RECOVERY_MSG = (
    "免费模型额度按**公网 IP** 限流已打满。按代价从低到高处理：\n"
    "1) 换公网 IP（小海未登录 zen，换 IP 即可续白嫖）：`scripts/oc_ip.py show` 看当前 IP 与换 IP 建议 "
    "→ 切飞行模式/重拨/换热点 → `scripts/oc_ip.py verify` 确认 IP 变了 → 重新跑本任务。\n"
    "2) 换 xiaomi/* 付费模型（用你的 key，要花钱）——需先问小海同意。\n"
    "3) 降级：把任务转给 ai-web-delegate（网页版免费 AI，出文本）或主 Agent 自己收尾。\n"
    "注意：opencode 走直连（`oc_run.py` 已自动剥离代理变量），HTTP 代理本就不参与 zen 调用，靠 HTTP 代理换 IP 无效，只能网络层换（5G 重连/宽带重拨/全局 tun VPN）。"
)


# --- 事件解析 -------------------------------------------------------------
def parse_perm_lines(text):
    """从文本（尤其是 stderr）里抓 headless 自动拒绝的权限提示行。"""
    out = []
    if not text:
        return out
    for line in text.splitlines():
        m = PERM_LINE_RE.search(line)
        if m:
            out.append({"permission": m.group(1), "pattern": m.group(2), "raw": line.strip()[:200]})
    return out


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_events(path):
    """解析 ndjson。注意三件事：
    1) opencode 出错时 exit code 仍是 0，只能靠 type=error 事件判断；
    2) 权限被自动拒绝的提示是**非 JSON 行**，必须单独抓；
    3) 工具可能 status=error（被 deny 或执行失败），任务会"表面成功"但活没干成。
    """
    texts, tools = [], []
    errors, perm_denied, failed_tools = [], [], []
    session_id = None
    cost = 0.0
    tokens = {"input": 0, "output": 0, "reasoning": 0, "total": 0}
    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        return {}, "日志文件不存在"
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if not line.startswith("{"):
                # 非 JSON 行：权限自动拒绝提示等
                m = PERM_LINE_RE.search(line)
                if m:
                    perm_denied.append({"permission": m.group(1), "pattern": m.group(2),
                                        "raw": line[:200]})
                elif "error" in line.lower():
                    errors.append({"source": "stdout_text", "message": line[:300]})
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            session_id = e.get("sessionID") or session_id
            t = e.get("type")
            p = e.get("part") or {}
            if t == "text":
                txt = p.get("text")
                if txt:
                    texts.append(txt)
            elif t == "tool_use":
                st = p.get("state") or {}
                inp = st.get("input") or {}
                summary = (inp.get("filePath") or inp.get("command")
                           or inp.get("pattern") or inp.get("path") or "")
                rec = {"tool": p.get("tool"), "status": st.get("status"),
                       "target": str(summary)[:200]}
                if st.get("status") == "error":
                    rec["error"] = str(st.get("error"))[:300]
                    failed_tools.append(rec)
                tools.append(rec)
            elif t == "step_finish":
                cost += float(p.get("cost") or 0)
                tk = p.get("tokens") or {}
                for k in ("input", "output", "reasoning"):
                    tokens[k] += int(tk.get(k) or 0)
                tokens["total"] = max(tokens["total"], int(tk.get("total") or 0))
            elif t == "error":
                err = e.get("error") or {}
                data = err.get("data") or {}
                errors.append({"source": "event", "name": err.get("name"),
                               "message": str(data.get("message") or err)[:300],
                               "ref": data.get("ref")})
    tool_counts = {}
    for t in tools:
        tool_counts[t["tool"]] = tool_counts.get(t["tool"], 0) + 1
    return {"session_id": session_id, "text": "\n".join(texts).strip(),
            "tools": tools[-40:], "tool_counts": tool_counts,
            "tokens": tokens, "cost": round(cost, 6),
            "errors": errors, "permission_denied": perm_denied,
            "failed_tools": failed_tools}, None


# --- 执行 -----------------------------------------------------------------
def build_cmd(a, prompt_text, session_id, model=None):
    # 注意顺序：prompt 必须放在 -f/--file 之前。
    # opencode run 会把 -f 之后的位置参数当成文件，导致把提示词误当成"要读的文件"而报 File not found。
    cmd = [opencode_bin(), "run", "--format", "json", "--dir", str(a.dir_resolved),
           "-m", model or a.model]
    if a.agent:
        cmd += ["--agent", a.agent]
    if session_id:
        cmd += ["-s", session_id]
    elif a.title:
        cmd += ["--title", a.title]
    cmd += [prompt_text]
    if a.attach:
        cmd += ["--attach", a.attach]
    if a.files:
        for f in a.files:
            cmd += ["--file", f]
    if a.auto:
        cmd += ["--auto"]
    return cmd


# --- 临时权限配置（硬约束，比提示词里写"禁止"靠谱） -------------------------
# 默认拦下的高危命令。实测：deny 规则连 --auto 都突破不了。
DEFAULT_DENY_BASH = [
    "rm *", "rmdir *", "sudo *", "shutdown *", "reboot *", "mkfs*", "dd *",
    "git push *", "git reset --hard*", "git clean *",
    "npm publish*", "yarn publish*", "pnpm publish*", "pip uninstall*",
    "curl * | sh", "curl * | bash", "wget * | sh", "wget * | bash",
    ":(){*", "chmod 777 *", "chown *",
]
COMMIT_DENY = ["git commit *"]


def build_perm_config(a, run_dir):
    """生成本次运行专用的临时 opencode 配置。
    只对本次生效（OPENCODE_CONFIG 环境变量），**不污染用户全局配置**。
    """
    if a.no_hardening and not a.allow_path:
        return None, {}
    perm = {}
    bash_rules = {"*": "allow"}
    if not a.no_hardening:
        for pat in DEFAULT_DENY_BASH:
            bash_rules[pat] = "deny"
        if not a.allow_commit:
            for pat in COMMIT_DENY:
                bash_rules[pat] = "deny"
        perm["bash"] = bash_rules
    if a.allow_path:
        ext = {}
        for p in a.allow_path:
            pat = p if any(c in p for c in "*?") else (p.rstrip("/") + "/*")
            ext[pat] = "allow"
            ext[p.rstrip("/")] = "allow"
        perm["external_directory"] = ext
    if not perm:
        return None, {}
    cfg = {"$schema": "https://opencode.ai/config.json", "permission": perm}
    path = run_dir / "opencode-perm.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path, perm


def _run_once(a, prompt_text, run_dir, model, session_id, log, err):
    """单次 opencode 调用：注入临时权限配置、解析事件流。

    关键事实（2026-08-10 实测）：
      * opencode run 的 exit code **永远是 0**，哪怕任务失败/限流——只能靠事件流判断。
      * headless 下权限请求被**自动拒绝**（非 JSON 行 `! permission requested: ...`），
        被拒的工具 status=error，任务会"表面跑完但活没干成"。
    所以成败判定完全基于解析结果，不理会 rc。
    """
    cfg_path, perm = build_perm_config(a, run_dir)
    cmd = build_cmd(a, prompt_text, session_id, model=model)
    env = dict(os.environ)
    # 2026-08-13 关键修复：WorkBuddy 的 Bash 沙箱会注入
    #   HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy = http://127.0.0.1:64969
    # 该代理到不了 opencode.ai/zen（curl 走代理→000 超时；直连→200）。
    # opencode CLI 会沿用这些代理变量 → 卡死在连 zen → 表现为"总是超时"。
    # 沙箱本身允许直连出口，所以这里直接剥离代理变量，让 opencode 走直连即可。
    # 剥离后本机直连 opencode.ai 实测 200，调用 6~12s 返回、cost:0。
    for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy", "no_proxy", "NO_PROXY"):
        env.pop(_pk, None)
    if cfg_path:
        env["OPENCODE_CONFIG"] = str(cfg_path)   # 仅本次生效，不污染全局配置

    t0 = time.time()
    timed_out = False
    stalled = False
    # 2026-08-14 根因修复：opencode 的 stdout 直接重定向到**文件**时会静默挂死——
    # 零事件、零输出，复现 60s 无任何输出（而 stdout=PIPE 时 5s 即返回）。原因疑似
    # opencode 检测到 stdout 非 TTY 非 PIPE 后走了异常分支。故改用 PIPE + 后台 pump
    # 线程中转：opencode 看到 PIPE 即正常流式输出，pump 线程实时落盘，stall 检测照旧看文件大小。
    proc = subprocess.Popen(cmd, cwd=str(a.dir_resolved), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    with open(log, "wb") as fo, open(err, "wb") as fe:
        def _pump(src, dst):
            try:
                while True:
                    # 必须用 read1 而非 read：read(n) 会阻塞到凑满 n 字节或 EOF 才返回，
                    # 而 opencode 分批小量写 stdout（实测 304B/712B/1078B 分批），read 会
                    # 干等凑满 → 数据迟迟不落盘 → stall 检测误判挂死。read1 有数据即返回。
                    chunk = src.read1(65536)
                    if not chunk:
                        break
                    dst.write(chunk)
                    dst.flush()
            except Exception:
                pass
        t_out = threading.Thread(target=_pump, args=(proc.stdout, fo), daemon=True)
        t_err = threading.Thread(target=_pump, args=(proc.stderr, fe), daemon=True)
        t_out.start(); t_err.start()
        # 零事件早停（2026-08-13 加）：免费模型经常"整只挂死"——进程活着但一个事件都不吐。
        # 若 stall_timeout 秒内事件流零增长，判定挂死并提前 kill，交给上层 failover 换模型，
        # 避免拿默认 900s 干等。有事件流的长任务不受影响（每来新事件就续命）。
        deadline = t0 + a.timeout
        stall_limit = max(30, getattr(a, "stall_timeout", 90) or 90)
        # 2026-08-14 修正：stall 分两档——首字节 stall_limit 秒内必须见输出（判真挂死）；
        # 一旦有输出，静默间隔放宽到 idle_stall（300s）。因为复杂构建任务里模型
        # "生成长文件内容/推理"会 >45s 不吐事件但并非挂死（实测 nemotron 生成文件 31~45s+ 被误杀）。
        idle_stall = max(stall_limit, 300)
        stall_deadline = t0 + stall_limit
        last_size = -1
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.time()
            if now >= deadline:
                proc.kill(); proc.wait(); rc = -1; timed_out = True
                break
            try:
                size = log.stat().st_size
            except OSError:
                size = 0
            if size > last_size:
                last_size = size
                stall_deadline = now + idle_stall
            elif now >= stall_deadline:
                proc.kill(); proc.wait(); rc = -1
                timed_out = True; stalled = True
                break
            time.sleep(1.0)
        t_out.join(timeout=5); t_err.join(timeout=5)
    dur = round(time.time() - t0, 1)

    parsed, perr = parse_events(log)
    stderr_text = ""
    try:
        stderr_text = err.read_text(errors="replace")
    except OSError:
        pass
    # headless 下权限自动拒绝提示走 **stderr**（不在 ndjson 里），必须单独抓
    parsed["permission_denied"] = parsed.get("permission_denied", []) + parse_perm_lines(stderr_text)
    stderr_tail = _ANSI_RE.sub("", stderr_text)[-800:]

    hard_fail = bool(parsed["errors"]) or timed_out
    permission_blocked = bool(parsed["permission_denied"])
    quota = detect_quota(parsed, stderr_tail)
    has_output = bool((parsed.get("text") or "").strip())
    failed_tools = parsed["failed_tools"]
    # 成败：硬错/超时/限流都算失败；有文本产出才算成；**只要有工具执行失败（被拒/出错）
    # 就绝不能算成功**——否则会"表面成功但活没干成"（rm 被拒、读文件被拒都属此类）。
    ok = ((not hard_fail) and (not timed_out) and (not quota)
          and has_output and (not failed_tools))

    warn = []
    if permission_blocked:
        pp = ", ".join(f"{p['permission']}({p['pattern']})" for p in parsed["permission_denied"])
        warn.append(f"⚠️ 权限被自动拒绝（headless 下无人应答）：{pp} —— 任务很可能没真正完成，见 permission_denied")
    if parsed["failed_tools"]:
        warn.append(f"⚠️ 有 {len(parsed['failed_tools'])} 个工具执行失败（可能权限被拒或命令出错），任务可能不完整")
    if quota:
        warn.append("⚠️ 疑似额度/限流打满（429/quota），见 errors / failed_tools / stderr_tail")

    partial = {
        "model": model, "session_id": parsed.get("session_id") or session_id,
        "reused_session": bool(session_id), "duration_s": dur, "timed_out": timed_out,
        "exit_code": rc, "text": parsed.get("text", ""), "tool_counts": parsed.get("tool_counts", {}),
        "tools": parsed.get("tools", []), "tokens": parsed.get("tokens", {}),
        "cost": parsed.get("cost", 0), "stderr_tail": stderr_tail.strip(),
        "errors": parsed.get("errors", []), "permission_denied": parsed.get("permission_denied", []),
        "failed_tools": parsed.get("failed_tools", []), "quota": quota,
        "hard_fail": hard_fail, "has_output": has_output, "ok": ok, "warnings": warn,
        "parse_error": perr, "stalled": stalled,
    }
    if stalled:
        warn.append(f"⚠️ 模型静默挂死：{stall_limit}s 内零事件，已提前 kill 并交给 failover 换模型"
                    f"（该模型今日疑似不可用，可考虑加入 DEAD_MODELS）")
    return partial, parsed


def do_run(a, prompt_text, run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt_text, encoding="utf-8")

    session_id = a.session
    if not session_id and a.reuse:
        if not a.title:
            die("--reuse 需要同时指定 --title（用于查回会话）")
        session_id = find_session(a.dir_resolved, a.title)
    did_reuse = bool(session_id)   # 仅当传入/查回既有会话才算"复用"，新建的不算

    snap = take_snapshot(a.dir_resolved, enabled=not a.no_snapshot)
    (run_dir / "snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
    t_start = time.time()

    # 模型尝试顺序：首选 a.model；失败时轮换其它存活免费模型（免费、无需人工）。
    # 2026-08-13 强化：failover 默认开启，且触发条件从"仅额度打满"扩展到**超时/静默挂死**——
    # 因为免费模型经常整只挂掉（零事件干等到超时），这是"调 opencode 老是超时"的主因之一。
    failover_on = a.auto_retry_quota or not a.no_failover
    multimodal_only = bool(a.files)
    MULTIMODAL_MODELS = {"opencode/mimo-v2.5-free"}  # 免费池唯一支持图片的模型
    if multimodal_only:
        # 看图任务：必须用支持图片的模型；failover 只在同一多模态模型内重试，
        # **绝不降级到非多模态**——否则会假成功（如 lightning 回 "I cannot read images" 却 ok=true）。
        primary = a.model if a.model in MULTIMODAL_MODELS else "opencode/mimo-v2.5-free"
        models = [primary]
        if "opencode/mimo-v2.5-free" not in models:
            models.append("opencode/mimo-v2.5-free")
    else:
        models = [a.model]
        if failover_on:
            for m in FREE_RETRY_ORDER:
                if m != a.model and m not in models:
                    models.append(m)
                if len(models) >= 1 + max(1, a.failover_max):
                    break

    final = None
    attempts = []
    base_timeout = a.timeout
    for i, model in enumerate(models):
        # 重试轮压缩超时：首轮给足，后续快速试探，避免总时长 = timeout × N 爆炸
        a.timeout = base_timeout if i == 0 else min(base_timeout, a.failover_timeout)
        log = run_dir / "out.ndjson" if i == 0 else run_dir / f"out_{i+1}.ndjson"
        err = run_dir / "err.log" if i == 0 else run_dir / f"err_{i+1}.log"
        partial, parsed = _run_once(a, prompt_text, run_dir, model, session_id, log, err)
        # 首轮若新建了会话，后续重试复用同一会话保上下文
        if not session_id and parsed.get("session_id"):
            session_id = parsed["session_id"]
        final = partial
        attempts.append({"attempt": i + 1, "model": model, "ok": partial["ok"],
                         "quota": partial["quota"], "duration_s": partial["duration_s"],
                         "timed_out": partial["timed_out"],
                         "permission_denied": bool(partial["permission_denied"]),
                         "failed_tools": len(partial["failed_tools"])})
        if partial["ok"]:
            break                      # 成功，停止轮换
        if not failover_on:
            break                      # 显式关了 failover，跑一次就收
        if partial["permission_denied"]:
            break                      # 权限越界：换模型也没用，且重跑有副作用风险
        # 可换模型自救的失败：额度打满 / 超时 / 零产出静默挂死
        retryable = (partial["quota"] or partial["timed_out"]
                     or (not partial["text"] and not partial["tool_counts"]))
        if not retryable:
            break                      # 其它真实失败（模型答错/工具失败）换模型无意义，省额度
    a.timeout = base_timeout

    changed, diff_info = diff_snapshot(a.dir_resolved, snap)
    # 最终成败：硬性失败/超时/限流都不算成功；有文本产出才算成；
    # 若磁盘确有改动但**有工具执行失败**，仍不算成功（防"表面成功活没干成"）。
    final_ok = (not final["hard_fail"]) and (not final["timed_out"]) and (not final["quota"]) \
        and (final["ok"] or (bool(changed) and not final["failed_tools"]))

    result = {
        "ok": final_ok,
        "run_id": run_dir.name, "dir": str(a.dir_resolved), "title": a.title,
        "model": final["model"], "agent": a.agent or "build",
        "session_id": final["session_id"], "reused_session": did_reuse,
        "duration_s": final["duration_s"], "timed_out": final["timed_out"],
        "exit_code": final["exit_code"], "text": final["text"],
        "tool_counts": final["tool_counts"], "tools": final["tools"],
        "tokens": final["tokens"], "cost": final["cost"],
        "files_changed": changed, "snapshot_type": snap.get("type"),
        "attempts": attempts, "retry_model_swap": len(attempts) > 1,
        "model_swapped_from": getattr(a, "model_swapped_from", None),
        "log_file": str(run_dir / "out.ndjson"),
    }
    if snap.get("note"):
        result["snapshot_note"] = snap["note"]
    result.update(diff_info)
    if final["stderr_tail"]:
        result["stderr_tail"] = final["stderr_tail"]
    if final["errors"]:
        result["errors"] = final["errors"]
    result["permission_denied"] = final["permission_denied"]
    result["failed_tools"] = final["failed_tools"]
    if final["warnings"]:
        result["warnings"] = final["warnings"]
    if final["parse_error"]:
        result["parse_error"] = final["parse_error"]

    if final_ok:
        result["next_step"] = ("外包已完成 → 必须质检：git -C %s diff / git status；核对 files_changed "
                               "是否都在允许范围内；跑项目测试验证。不合格用 --reuse 同会话追问修正。"
                               % a.dir_resolved)
    else:
        result["quota_exhausted"] = final["quota"]
        if final["quota"]:
            result["next_step"] = QUOTA_RECOVERY_MSG
        elif final["permission_denied"]:
            result["next_step"] = ("任务因权限被自动拒绝而失败。处理：① 若是要读/写 --dir 外的合法路径，"
                                   "用 --allow-path <目录> 精确放行该目录后重跑；② 若是高危命令被硬拦截，"
                                   "说明任务本身越界，应调整提示词或换方案；③ 切勿用 --auto 绕过（会放开整个沙箱）。")
        else:
            result["next_step"] = ("外包未成功完成（见 errors / failed_tools / stderr_tail / warnings）。"
                                   "可换模型重试、用 --reuse 同会话追问、或主 Agent 自己收尾。")

    (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {"dir": str(a.dir_resolved), "title": a.title, "model": final["model"],
            "agent": a.agent or "build", "reused_session": did_reuse,
            "started_at": t_start, "finished_at": time.time(),
            "status": "done" if final_ok else ("timeout" if final["timed_out"] else "failed"),
            "attempts": attempts}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return result


def do_status(run_id):
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        die(f"找不到 run: {run_id}", runs_dir=str(RUNS_DIR))
    res_file = run_dir / "result.json"
    if res_file.exists():
        emit(json.loads(res_file.read_text()))
        return
    meta = {}
    try:
        meta = json.loads((run_dir / "meta.json").read_text())
    except Exception:
        pass
    if not meta:
        # meta 还没落盘：可能刚启动，也可能 worker 直接崩了
        wlog = ""
        try:
            wlog = (run_dir / "worker.log").read_text(errors="replace").strip()
        except OSError:
            pass
        if wlog:
            emit({"ok": False, "run_id": run_id, "status": "worker_failed",
                  "error": "后台 worker 启动失败", "worker_log": wlog[-1200:]})
            return
    parsed, _ = parse_events(run_dir / "out.ndjson")
    elapsed = round(time.time() - meta.get("started_at", time.time()), 1)
    try:
        no_events = (run_dir / "out.ndjson").stat().st_size == 0
    except OSError:
        no_events = True
    out = {"ok": True, "run_id": run_id, "status": "running", "elapsed_s": elapsed,
           "title": meta.get("title"), "model": meta.get("model"), "dir": meta.get("dir"),
           "partial_text": (parsed.get("text") or "")[-1500:],
           "tool_counts": parsed.get("tool_counts", {}),
           "log_file": str(run_dir / "out.ndjson"),
           "hint": "仍在跑。稍后再用 --status 查；别用轮询死等。"}
    if no_events and elapsed > 120:
        out["warning"] = (f"⚠️ 已跑 {elapsed}s 但事件流零输出，模型 {meta.get('model')} 很可能静默挂死。"
                          f"建议：pkill -f \"{meta.get('model')}\" 后换模型重跑，并在 references/experience.md 记一笔。")
    emit(out)


def slug(s):
    s = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", (s or "task")).strip("-")
    return (s[:24] or "task")


def main():
    ap = argparse.ArgumentParser(description="把编码任务外包给 opencode（安全封装）")
    ap.add_argument("--dir", dest="dir")
    ap.add_argument("--prompt")
    ap.add_argument("--prompt-file")
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL)
    ap.add_argument("--agent", choices=["build", "plan", "general", "explore"])
    ap.add_argument("--title")
    ap.add_argument("--session")
    ap.add_argument("--reuse", action="store_true", help="按 title+dir 查回已有会话续跑")
    ap.add_argument("--bg", action="store_true", help="后台跑，立即返回 run_id")
    ap.add_argument("--status", help="查询后台任务：run_id")
    ap.add_argument("--list", action="store_true", help="列出该目录的历史会话")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--attach", help="附着到 opencode serve，如 http://localhost:4096")
    ap.add_argument("--file", action="append", dest="files", default=None,
                    help="附加文件到消息（多模态：图片等）；可多次指定")
    ap.add_argument("--auto", action="store_true", help="自动批准权限请求（危险，默认关）")
    ap.add_argument("--no-snapshot", action="store_true")
    ap.add_argument("--allow-unsafe-dir", action="store_true")
    ap.add_argument("--allow-path", action="append", dest="allow_path", default=None,
                    help="精确放行某目录的越界读写（写进临时配置，不污染全局）；可多次指定")
    ap.add_argument("--no-hardening", action="store_true",
                    help="关闭高危命令硬拦截（默认开启，强烈不建议关）")
    ap.add_argument("--allow-commit", action="store_true",
                    help="允许 opencode 执行 git commit（默认禁止）")
    ap.add_argument("--auto-retry-quota", action="store_true",
                    help="额度打满时自动轮换其它免费模型重试（仍可能需换 IP，见 next_step）")
    ap.add_argument("--no-failover", action="store_true",
                    help="关闭失败自动换模型（默认开启：超时/挂死/额度满会自动换存活免费模型重试）")
    ap.add_argument("--failover-max", type=int, default=3,
                    help="最多额外尝试几个备用模型（默认 3；免费池波动大，多备一个）")
    ap.add_argument("--failover-timeout", type=int, default=120,
                    help="重试轮的单次超时上限秒（默认 120，防总时长爆炸）")
    ap.add_argument("--stall-timeout", type=int, default=45,
                    help="事件流零增长多少秒判定模型挂死并提前换模型（默认 45；长任务有事件流不受影响）")
    ap.add_argument("--force-model", action="store_true",
                    help="强行使用 -m 指定的模型，即使它在 DEAD_MODELS 黑名单里（用于复活验证）")
    ap.add_argument("--_worker", help=argparse.SUPPRESS)
    a = ap.parse_args()

    # 挂死/下架模型防呆：显式指定黑名单模型时自动换成默认首选，避免白等一个超时。
    # 这是"调 opencode 老是超时"的头号坑（如 deepseek-v4-flash-free 已挂死）。
    a.model_swapped_from = None
    if a.model in DEAD_MODELS and not a.force_model:
        a.model_swapped_from = a.model
        a.model = DEFAULT_MODEL

    if a.status:
        do_status(a.status)
        return

    d, warnings = check_dir(a.dir, a.allow_unsafe_dir)
    a.dir_resolved = d

    if a.list:
        emit({"ok": True, "dir": str(d),
              "sessions": [s for s in list_sessions(d, 50) if s.get("directory") and same_dir(s["directory"], d)]})
        return

    if a.prompt_file:
        pf = Path(a.prompt_file).expanduser()
        if not pf.exists():
            die(f"提示词文件不存在: {pf}")
        prompt_text = pf.read_text(encoding="utf-8")
    elif a.prompt:
        prompt_text = a.prompt
    else:
        die("必须提供 --prompt 或 --prompt-file")
    if not prompt_text.strip():
        die("提示词为空")

    if not shutil.which("opencode") and not Path("/opt/homebrew/bin/opencode").exists():
        die("找不到 opencode 命令，先确认已安装（brew / curl -fsSL https://opencode.ai/install | bash）")

    if a._worker:
        run_dir = RUNS_DIR / a._worker
        do_run(a, prompt_text, run_dir)
        return

    stamp = time.strftime("%m%d-%H%M%S")
    run_id = f"{stamp}-{slug(a.title)}-{uuid.uuid4().hex[:4]}"
    run_dir = RUNS_DIR / run_id

    if a.bg:
        run_dir.mkdir(parents=True, exist_ok=True)
        pf = run_dir / "prompt.md"
        pf.write_text(prompt_text, encoding="utf-8")
        cmd = [sys.executable, os.path.abspath(__file__), "--_worker", run_id,
               "--dir", str(d), "--prompt-file", str(pf), "-m", a.model,
               "--timeout", str(a.timeout),
               "--failover-max", str(a.failover_max),
               "--failover-timeout", str(a.failover_timeout),
               "--stall-timeout", str(a.stall_timeout)]
        for flag, val in (("--agent", a.agent), ("--title", a.title),
                          ("--session", a.session), ("--attach", a.attach)):
            if val:
                cmd += [flag, val]
        if a.files:
            for f in a.files:
                cmd += ["--file", f]
        for flag, on in (("--reuse", a.reuse), ("--no-snapshot", a.no_snapshot),
                         ("--auto", a.auto), ("--allow-unsafe-dir", a.allow_unsafe_dir),
                         ("--no-hardening", a.no_hardening), ("--allow-commit", a.allow_commit),
                         ("--auto-retry-quota", a.auto_retry_quota),
                         ("--no-failover", a.no_failover),
                         ("--force-model", a.force_model)):
            if on:
                cmd.append(flag)
        if a.allow_path:
            for p in a.allow_path:
                cmd += ["--allow-path", p]
        with open(run_dir / "worker.log", "wb") as wl:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=wl,
                             stderr=subprocess.STDOUT,
                             start_new_session=True, cwd=str(d))
        out = {"ok": True, "mode": "background", "run_id": run_id, "dir": str(d),
               "title": a.title, "model": a.model,
               "check": f"python3 {os.path.abspath(__file__)} --status {run_id}",
               "log_file": str(run_dir / "out.ndjson"),
               "hint": "任务已后台启动。过一会儿用 --status 查，别死等轮询。"}
        if warnings:
            out["warnings"] = warnings
        emit(out)
        return

    result = do_run(a, prompt_text, run_dir)
    if warnings:
        result["warnings"] = warnings
    emit(result)


if __name__ == "__main__":
    main()
