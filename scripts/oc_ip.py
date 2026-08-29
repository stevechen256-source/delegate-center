#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
oc_ip.py — 出口公网 IP 查询 / 换 IP 引导 / 换 IP 验证。

用途：opencode zen 免费模型按**公网 IP** 限流（社区实证 + 官方文档口径"IP 级别的配额"）。
额度打满（429）时，换一个公网 IP 即可继续白嫖——前提是没登录 zen 账号（小海就是这种情况）。

子命令：
  show     查当前出口 IP + 归属地 + 网络类型判断，并给出针对性的换 IP 建议
  wait     记下当前 IP，轮询等待它变化（用户去切飞行模式/重连热点），变了就返回
  verify   对比上次记录的 IP，判断是否真的换了
  probe    换完 IP 后，实际调一次免费模型验证额度是否恢复

用法:
  python3 oc_ip.py show
  python3 oc_ip.py wait --timeout 180
  python3 oc_ip.py probe
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

STATE = Path.home() / ".workbuddy" / ".cache" / "opencode-delegate" / "ip_state.json"
# 多个源，避免单点失败/被墙
IP_SOURCES = [
    ("https://api.ipify.org", "text"),
    ("https://ifconfig.me/ip", "text"),
    ("https://ipinfo.io/ip", "text"),
    ("https://myip.ipip.net", "text"),
]
GEO_URL = "https://ipinfo.io/json"


def emit(o):
    print(json.dumps(o, ensure_ascii=False, indent=2))


def curl(url, timeout=8):
    """裸 curl，显式绕开 *_proxy 环境变量，拿真实出口 IP"""
    try:
        r = subprocess.run(
            ["curl", "-s", "--noproxy", "*", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 4)
        return r.stdout.strip()
    except Exception:
        return ""


def get_ip():
    import re
    for url, _ in IP_SOURCES:
        out = curl(url)
        m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", out or "")
        if m:
            return m.group(1), url
    return None, None


def get_geo():
    out = curl(GEO_URL, 8)
    try:
        return json.loads(out)
    except Exception:
        return {}


def net_hint(geo):
    """按网络类型给针对性的换 IP 建议（越靠前越省事）"""
    org = (geo.get("org") or "").lower()
    tips = []
    is_mobile = any(k in org for k in ("5g", "4g", "mobile", "cmcc", "unicom mobile", "wireless"))
    if is_mobile:
        tips.append("📶 检测到**移动/5G 网络** → 换 IP 最容易："
                    "开飞行模式 10 秒再关（或重启随身 WiFi / 5G CPE / 手机热点），"
                    "运营商基本会重新分配公网 IP。成功率高、耗时 ~30 秒。")
    tips += [
        "🔌 家宽/光猫：断开重拨 PPPoE（或路由器断电 1~2 分钟再上电），动态 IP 通常会变。",
        "📱 换热点：手机热点 ↔ 家里 WiFi 互切，是两条完全不同的出口线路。",
        "🌐 全局 VPN/tun 模式：⚠️ opencode 走直连（`oc_run.py` 已自动剥离代理变量），HTTP 代理不参与 zen 调用，"
        "只有工作在网络层的全局 tun 代理才有效，普通 HTTP 代理无效。",
    ]
    return tips


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(d):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_show(_a):
    ip, src = get_ip()
    if not ip:
        emit({"ok": False, "error": "拿不到公网 IP（网络不通？几个查询源都失败了）"})
        return
    geo = get_geo()
    st = load_state()
    prev = st.get("ip")
    save_state({"ip": ip, "at": time.time(),
                "geo": {k: geo.get(k) for k in ("city", "region", "country", "org")}})
    out = {"ok": True, "ip": ip, "source": src,
           "geo": {k: geo.get(k) for k in ("city", "region", "country", "org") if geo.get(k)},
           "previous_ip": prev,
           "changed_since_last_check": bool(prev and prev != ip),
           "how_to_change_ip": net_hint(geo)}
    emit(out)


def cmd_wait(a):
    old, _ = get_ip()
    if not old:
        emit({"ok": False, "error": "拿不到当前 IP，先检查网络"})
        return
    print(json.dumps({"status": "waiting", "current_ip": old,
                      "action_needed": "请现在去切换网络（飞行模式开关 / 重启热点 / 路由器重拨），"
                                       "本命令会自动轮询，IP 一变就返回。",
                      "timeout_s": a.timeout}, ensure_ascii=False, indent=2), flush=True)
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        time.sleep(a.interval)
        new, _ = get_ip()
        if new and new != old:
            geo = get_geo()
            save_state({"ip": new, "at": time.time()})
            emit({"ok": True, "changed": True, "old_ip": old, "new_ip": new,
                  "waited_s": round(time.time() - t0, 1),
                  "geo": {k: geo.get(k) for k in ("city", "region", "org") if geo.get(k)},
                  "next": "IP 已变。用 `oc_ip.py probe` 实测免费模型额度是否恢复。"})
            return
    emit({"ok": False, "changed": False, "ip": old,
          "error": f"等了 {a.timeout}s，IP 没变",
          "hint": "有些运营商短时间重连会分回同一个 IP：等 2~5 分钟再试，"
                  "或换一条完全不同的线路（手机热点 ↔ 宽带）。"})


def cmd_verify(_a):
    st = load_state()
    prev = st.get("ip")
    ip, _ = get_ip()
    save_state({"ip": ip, "at": time.time()})
    emit({"ok": True, "previous_ip": prev, "current_ip": ip,
          "changed": bool(prev and ip and prev != ip),
          "note": "changed=false 说明还是老 IP，额度大概率仍然是满的（被限状态）"})


def cmd_probe(a):
    """实际调一次免费模型，验证额度是否恢复"""
    import tempfile
    ip, _ = get_ip()
    d = tempfile.mkdtemp(prefix="oc_ip_probe_")
    t0 = time.time()
    r = subprocess.run(["opencode", "run", "--dir", d, "--format", "json",
                        "-m", a.model, "Reply with exactly: PONG"],
                       cwd=d, capture_output=True, text=True, timeout=180)
    blob = (r.stdout or "") + (r.stderr or "")
    low = blob.lower()
    limited = any(k in low for k in ("rate limit", "429", "quota", "too many requests",
                                     "exceeded", "insufficient"))
    got = "PONG" in blob
    emit({"ok": got and not limited, "ip": ip, "model": a.model,
          "elapsed_s": round(time.time() - t0, 1),
          "quota_available": got and not limited,
          "rate_limited": limited,
          "raw_tail": blob[-400:] if (limited or not got) else None,
          "verdict": ("✅ 额度可用，可以继续外包" if got and not limited else
                      "❌ 仍被限流 / 无响应 → 再换一次 IP，或换个免费模型，或走降级链")})


def main():
    ap = argparse.ArgumentParser(description="opencode 出口 IP 查询与切换助手")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("show").set_defaults(func=cmd_show)
    w = sub.add_parser("wait")
    w.add_argument("--timeout", type=int, default=300)
    w.add_argument("--interval", type=int, default=6)
    w.set_defaults(func=cmd_wait)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    p = sub.add_parser("probe")
    p.add_argument("-m", "--model", default="opencode/laguna-s-2.1-free")
    p.set_defaults(func=cmd_probe)
    a = ap.parse_args()
    if not getattr(a, "func", None):
        a.cmd = "show"
        cmd_show(a)
        return
    a.func(a)


if __name__ == "__main__":
    main()
