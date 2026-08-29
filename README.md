# delegate-center · 统一外包中心

> 把重复、量大、规则明确的活，甩给**免费 AI** 干。主 Agent 只做三件事：**拆任务、定标准、验收**。

一个给 AI Agent 用的"任务外包调度层"。它解决的是一个很具体的钱的问题——

> 主 Agent 的模型很贵（按 token 计费），而外界存在大量**免费且够用**的 AI 算力：本地 CLI 的免费模型池、网页版的免费对话。
> 问题是这些免费资源**不稳定**（模型按周上下线、额度按 IP 限流、权限会静默拒绝、工具调用会假装成功）。
> `delegate-center` 就是把这些不确定性全部兜住，让"外包"变成一条可以直接调用的命令。

---

## 它解决什么

| 不用它 | 用了它 |
|--------|--------|
| 主 Agent 亲自写 3000 行模板代码，烧掉大量 token | 一条命令甩给免费模型，主 Agent 只审 diff |
| 免费模型挂死 → 卡 4 分钟 → 报"超时" → 放弃外包 | 45s 零事件判死 → 自动 kill → 换下一个模型（最多再试 3 个） |
| 不知道哪个免费模型还活着（名单每周变） | 内置模型池 + 黑名单 + 探活脚本，串行实测后自动轮换 |
| headless 下权限被自动拒绝，AI "假装跑完" | 如实上报 `permission_denied` / `failed_tools`，`ok` 判定硬化 |
| 外包完不知道改了哪些文件 | 跑前快照 + 跑后 `files_changed`，逐项审 |

---

## 架构：两条通道

| 任务长这样 | 走哪条 | 为什么 |
|-----------|--------|--------|
| 批量改文件、补测试、写脚本、代码迁移、生成文档 | **A · 本地 OpenCode CLI** | 能直接落盘、跑命令，免费模型 `cost: 0` |
| 文章/文案/方案/翻译/报告/长内容 | **B · 网页版免费 AI** | 出文本强，主 Agent 只搬运结果 |
| 看图 / OCR / 截图改样式 | **A · `mimo-v2.5-free`** | 免费池唯一多模态，用 `--file` 传图 |
| 既要生成又要落盘 | **先 B 出文本 → 再 A 落盘** | 两段式，各取所长 |

不确定走哪条？**默认走 A** —— 它能落盘、可质检、零搬运。

---

## 快速开始

### 前置

- 通道 A：[OpenCode](https://opencode.ai) CLI（`brew install opencode`）
- 通道 B：Chrome 以 `--remote-debugging-port=9222` 启动，并登录 DeepSeek / 豆包 / 千问
- Python 3.11+

### 通道 A：一条命令外包

```bash
PY=/path/to/python3
OC=/path/to/delegate-center/scripts/oc_run.py

# 标准外包（最常用）—— 不指定 -m，交给默认模型 + 自动 failover
$PY $OC --dir /path/to/project --title "补 utils 单测" --prompt-file /tmp/task.md

# 只读分析（零写入风险）
$PY $OC --dir /path/to/project --agent plan --prompt "通读 src/，列出 3 个最值得优化的点"

# 后台长任务（>2min）
$PY $OC --dir /path/to/project --title "重构鉴权" --prompt-file /tmp/task.md --bg
$PY $OC --status <run_id>

# 多轮追问（复用会话，省 ~28 倍 input token）
$PY $OC --dir /path/to/project --title "补 utils 单测" --reuse --prompt "第 3 个用例边界值应是 0 不是 1"

# 看图
$PY $OC --dir /path/to/project -m opencode/mimo-v2.5-free --file /tmp/shot.png --prompt "按这张截图改样式"
```

返回结构化 JSON，先看 `ok` 字段：

```jsonc
{
  "ok": true,
  "text": "...",
  "files_changed": ["src/utils.py"],
  "cost": 0,
  "attempts": [{ "model": "opencode/nemotron-3.5-lightning-free", "duration_s": 22.1, "ok": true }],
  "permission_denied": [],
  "failed_tools": [],
  "next_step": "外包已完成 → 必须质检：git diff / git status ..."
}
```

### 通道 B：网页版出文本

没有单条命令，按 4 步走：连 CDP（9222）→ 复用同一对话 → 发送并轮询 → 取结果（优先「复制」按钮 + 读剪贴板）。
细节见 `references/providers.md`（站点结构/选择器/登录）与 `references/operations.md`（CDP 片段/已知坑）。

---

## 核心机制

### 1. 模型池自动轮换（通道 A）

免费模型名单**按周滚动**。曾经的默认首选 `deepseek-v4-flash-free` 挂死、`laguna-s-2.1-free` 下架——这类变动是**静默**的，报错信息（`server error`）看不出根因。

所以内置三层防御：

- `DEFAULT_MODEL` + `FREE_RETRY_ORDER`：按顺序轮换 6 个实测可用的免费模型
- `DEAD_MODELS` 黑名单：显式指定已死模型会被自动替换（`--force-model` 可强行验证复活）
- stall 检测：首字节 45s 零事件判死 → kill → 换模型；有输出后放宽到 300s（防误杀长内容生成）

**当前模型池**（2026-08-29 串行实测）：

| 模型 | 往返 | 说明 |
|------|------|------|
| `opencode/nemotron-3.5-lightning-free` | 9.7s | 默认，稳定 |
| `opencode/ling-3.0-flash-fin-free` | 6.3s | 最快（新） |
| `opencode/hy3-free` | 4.9s | 曾挂死，已复活 |
| `opencode/mimo-v2.5-free` | 10.5s | 唯一多模态 |
| `opencode/nemotron-3-ultra-free` | 31.1s | 复杂推理 |
| `opencode/big-pickle` | ~60s | 未标 free，兜底 |

复核命令：`scripts/oc_probe.sh`（⚠️ **必须串行**，并行会假性全挂）。

### 2. 安全沙箱（三重）

1. **`--dir` 是硬边界**：脚本内置黑名单，拒跑 `$HOME` / `Desktop` / `Downloads` / `~/.ssh` / `~/.config`。合法越界用 `--allow-path`（仅本次生效，不污染全局配置）。
2. **跑前快照 + 跑后 diff**：git HEAD 或文件指纹 → 输出 `files_changed`。
3. **高危命令硬拦截**：`rm` / `git push` / `git reset --hard` / `sudo` / `curl | sh` 默认 deny。**从不使用 `--auto`**（那会放开整个沙箱）。

### 3. 成败判定硬化

headless `opencode run` 下 bash **零批准直接执行**，而任何需批准的权限**不是卡住，是被自动拒绝**——模型会"假装跑完"。

所以 `ok` 的判定是：

```
ok = 非硬错 ∧ 非超时 ∧ 非限流 ∧ (有文本产出 ∨ (磁盘有改动 ∧ 无工具失败))
```

**永远不要只看返回文本判断成功。**

---

## 踩过的坑（都修进代码了）

历史上有 4 个"调 opencode 总超时"的根因，现已全部解决：

1. **沙箱代理注入**：Bash 沙箱注入 `HTTP_PROXY=...64969` 到不了后端 → 脚本自动剥离四个代理变量
2. **stdout 重定向到文件 → 静默挂死**：改为 `subprocess.PIPE` 中转
3. **`read(65536)` 阻塞凑满**：opencode 分批小量写，凑满才返回 → 实时性丢失、stall 误判。改为 `read1(n)`
4. **stall 45s 误杀复杂任务**：长内容生成 >45s 静默是正常 → 改首字节 45s + 有输出后 300s

其他：

- **多 attempt 覆盖风险**：attempt1 成功却被判超时 → failover 重跑会覆盖成果。发现 `out_2/out_3.ndjson` 且 attempt1 已成功 → 立即 kill worker
- **看图参数顺序**：`--prompt` 必须写在 `--file` **之前**，反了会报 `File not found: <提示词>`
- **额度按公网 IP 限流**：换 IP 只能走网络层（5G 重连/宽带重拨/全局 tun VPN），HTTP 代理无效（因为 opencode 走直连）

---

## 目录结构

```
delegate-center/
├── README.md
├── SKILL.md                 # 完整的调度 SOP（给 Agent 读的）
├── references/
│   ├── models.md            # 模型池实测快照 + 探活纪律
│   ├── patterns.md          # 提示词剧本 / CLI / 权限机制
│   ├── experience.md        # 踩坑经验流水
│   ├── providers.md         # 通道 B：站点结构/选择器/登录
│   └── operations.md        # 通道 B：CDP 片段/已知坑/过验证码
└── scripts/
    ├── oc_run.py            # 通道 A 主力入口（所有本地外包走它）
    ├── oc_probe.sh          # 模型探活测速
    ├── oc_ip.py             # 出口 IP 查询 / 换 IP 引导
    └── cdp_*.mjs            # 通道 B 浏览器操作（真实键鼠/文件/裁剪）
```

---

## 与 Agent 框架的关系

本项目最初作为 [WorkBuddy](https://www.workbuddy.cn) 的一个 skill 运行（`SKILL.md` 就是调度 SOP）。
但核心逻辑不依赖任何特定框架：

- `scripts/oc_run.py` 是独立的 Python CLI，任何能跑 shell 的 Agent 都能调
- `SKILL.md` 里的路由规则改写成自己的 system prompt 即可
- 通道 B 只依赖 Chrome DevTools Protocol，可移植到 Playwright/Puppeteer

---

## 路线图

- [ ] 成本看板：统计每日节省的 token、各通道成功率、模型可用率
- [ ] 通道 B 的自动登录检测与恢复
- [ ] 模型池自动探活 + 自动更新 `DEAD_MODELS`（当前是手动跑脚本）
- [ ] 多模型交叉评审（A 生成 → B 严格审稿 → 合并）的流程化封装

---

## 许可

MIT © stevechen256-source
