---
name: delegate-center
description: 统一外包中心——把重活外包给「免费 AI」以省主 Agent 的 token，双通道自动路由。通道 A：本地 OpenCode CLI（免费模型，能直接读写文件/跑命令/改代码仓库，落盘类编码活）；通道 B：浏览器网页版免费 AI（DeepSeek/豆包/千问，出文本/文案/长内容/多模型交叉优化）。触发场景：用户说"外包/交给 AI 写/省点 token 让别的 AI 干/丢给网页版 DeepSeek/豆包"、判断某段重复性编码或长内容工作值得外包。含路由决策、安全沙箱纪律、权限处理（headless 自动拒绝三档）、额度耗尽应对链（换 IP/换模型/降级）、会话复用、结果质检。
agent_created: true
---

# 统一外包中心（delegate-center）

> ## 🔴 第 0 条：默认外包，不是默认自己干
>
> **只有命中这 5 条白名单才允许自己干**：**W1** 纯问答且 <300 字且不产生文件 / **W2** 只读操作 / **W3** 单文件改 ≤20 行 / **W4** 含独家上下文（会话状态·身份口吻·私密·记忆技能自身维护）/ **W5** 已外包但连续 3 个模型全 `ok:false`。
>
> **其余一切先调 opencode**。动手前自检：**「我自己写——命中第几条 W？」说不出条号 → 停手，先外包。**
>
> **量化触发线**（命中任一 = 必须外包）：产出 ≥300 字 / ≥2 个文件 / 单文件改 ≥20 行 / 需新建文件 / 重构·批量替换·补测试 / 研究·方案对比·审查·找根因 / 看图 / "感觉有点麻烦"。
>
> **最简调用（照抄，不用 `-m`、不用 `env -u`）**：
> ```bash
> PY=$PY
> OC=$DELEGATE_CENTER/scripts/oc_run.py
> $PY $OC --dir <项目> --prompt "<任务+成功标准>"
> ```
> 变量只需设一次：`DELEGATE_CENTER` = 本仓库目录，`PY` = python3（3.11+）。
>
> ❌ **"超时"不再是跳过外包的理由**——代理已自动剥离、挂死模型已黑名单、45s 零事件自动换模型。分级细则见 `delegate-first` skill。

> **一句话**：把「重复量大、规则明确、能外包」的活，甩给免费 AI 干，主 Agent 只做拆任务、定标准、验收。两条通道，按任务类型路由：

| 任务长这样 | 走哪个通道 | 为什么 |
|-----------|-----------|--------|
| 批量改文件、补测试、写脚本/工具、代码迁移翻译、生成文档注释、跑分析 | **A. 本地 OpenCode** | 能直接落盘、跑命令，免费模型 `cost=0` |
| 文章/文案/方案/翻译/报告/长内容、需要多模型互相评审润色 | **B. 网页版 AI** | 浏览器免费 AI 出文本强，主 Agent 只搬运结果 |
| 需要**看图/识别图片**（截图改样式、图中代码） | **A. OpenCode `mimo-v2.5-free`**（免费唯一多模态） | 本地多模态，用 `--file` 传图 |
| 既要生成又要落盘（如：网页 AI 写代码 → 本地 opencode 应用/跑测试） | **先 B 出文本，再 A 落盘** | 两段式，各取所长 |

> 不确定走哪条？**默认优先 A（本地 opencode）**——它能落盘、可质检、零搬运；纯文本/创作类才走 B。

### 常见场景 → 直接调用（抄命令）

**通道 A（本地 opencode，落盘编码）** —— 先设变量（一次性）：

```bash
OC=$DELEGATE_CENTER/scripts/oc_run.py
PY=$PY
```

| 场景 | 直接调用（在上方变量后拼接） |
|------|----------|
| 让 AI 改/写项目代码（最常用） | `$PY $OC --dir <项目目录> --title "任务名" --prompt-file /tmp/task.md` |
| 只读分析、不动文件 | `$PY $OC --dir <项目目录> --agent plan --prompt "通读 src/，列出 3 个最该优化的点"` |
| 长任务不想干等（>2min） | 末尾加 `--bg`，再用 `$PY $OC --status <run_id>` 查进度 |
| 同一任务追问（保留上下文） | 末尾加 `--reuse --prompt "把第 3 个用例边界值改成 0"` |
| 需读/写 `--dir` 外的合法目录 | 末尾加 `--allow-path <那个目录>`（仅本次生效，不污染全局） |
| 看图（截图改样式/图中代码） | `-m opencode/mimo-v2.5-free --file /tmp/shot.png --prompt "按图改样式"` |
| 模型挂死/额度满 | **默认已自动处理**：45s 零事件即判挂死 → 自动换存活模型（最多再试 3 个）。关掉用 `--no-failover`；调阈值用 `--stall-timeout <秒>` |
| 想强行用黑名单里的挂死模型（复活验证） | 加 `--force-model` |

> `--prompt-file` 比 `--prompt` 更适合长任务书：把需求写进 `/tmp/task.md`，AI 看到的更完整、不丢字。

**通道 B（网页版 AI，出文本/文案）** —— 没有单条命令，按这 4 步走：
1. 加载 `web-access`（起 CDP Proxy，连 Chrome debug 9222）→ 开 DeepSeek/豆包/千问 后台 tab；
2. 同一任务**复用同一对话**（首条消息用「任务名」起标题，续聊按任务名搜索进入；换对话加角标 `任务名-续2`）；
3. 写提示词 → 发送 → 轮询生成结束 → 取结果（优先「复制」按钮 + 读剪贴板）；
4. 提质量用模式 B：把初稿丢给另一个 AI 当严格审稿人重写，主 Agent 合并两版。

> 通道 B 关键：登录态在 9222 的 Chrome debug，未登录请用户手动登；浏览器操作细节见 `references/operations.md`。

---

## 通道 A：本地 OpenCode（落盘编码，免费 cost=0）

> 详见 `references/models.md`（模型路由/多模态）、`references/patterns.md`（提示词剧本/CLI/权限机制）、`references/experience.md`（踩坑经验）。

> ### ⚠️ WorkBuddy 调用必读：为什么之前"总是超时"（4 个根因已全部定位+修复）
> 历史上"调 opencode 老是超时/挂死"共踩过 **4 个根因**，现已全部修进 `oc_run.py`，**直接正常调用即可**，不用手动 `env -u`、不用 `dangerouslyDisableSandbox`：
> 1. **沙箱代理**（08-13 定位）：Bash 沙箱注入 `HTTP_PROXY/HTTPS_PROXY/...=http://127.0.0.1:64969`，到不了 zen。→ `oc_run.py` 已自动剥离四代理变量。
> 2. **stdout 重定向到文件 → 静默挂死**（08-14 定位）：opencode 的 stdout 直接指向文件时零输出 60s。→ 已改 `subprocess.PIPE` 中转。
> 3. **PIPE 用 read(n) 阻塞凑满**（08-14 定位，最致命）：`read(65536)` 会干等凑满才返回，opencode 分批小量写导致实时性丢失、stall 误判。→ 已改 `read1(n)`。
> 4. **stall 45s 误杀复杂任务**（08-14 定位）：模型生成长内容 >45s 静默是正常，被误判挂死。→ 改首字节 45s + 有输出后 300s 间隔。
> - **实测基线**（08-14 修复后）：纯文本 8s 返回、`cost:0`；复杂构建 51~98s 单次 attempt 完成（不再 4 模型轮换）。
> - **若仍超时**：按本文件末尾「超时/挂死 自救决策树」逐层排查，别只回一句"超时"。

**本机环境**：`/opt/homebrew/bin/opencode` CLI v1.18.14（桌面 App v1.18.21）；`opencode/*` 免费池当前 7 个在列表、**6 个实测可用**、`cost:0`（2026-08-29 串行实测，详见 `references/models.md`）；已认证 `xiaomi`（付费，小海送的额度不多）。OpenCode 桌面 App 常驻，本地还跑着一个 web 服务（端口见「端口地图」）。

### 用法（优先用封装脚本，别手搓）

```bash
OC=$DELEGATE_CENTER/scripts/oc_run.py
PY=$PY

# 标准外包（最常用）—— 不指定 -m，交给默认模型 + 自动 failover
$PY $OC --dir /path/to/project --title "补 utils 单测" --prompt-file /tmp/task.md

# 只读分析（零写入风险）
$PY $OC --dir /path/to/project --agent plan --prompt "通读 src/，列出 3 个最值得优化的点"

# 后台长任务（>2min 不阻塞）
$PY $OC --dir /path/to/project --title "重构鉴权" --prompt-file /tmp/task.md --bg
$PY $OC --status <run_id>

# 多轮追问（复用同一会话，上下文保留）
$PY $OC --dir /path/to/project --title "补 utils 单测" --reuse --prompt "第 3 个用例边界值应是 0 不是 1，修正"

# 跨目录合法读写 → 精确放行（仅本次生效，不污染全局）
$PY $OC --dir /path/to/project --allow-path /path/to/another --prompt "读 /path/to/another/conf.json 并说明结构"

# 看图（免费多模态 mimo-v2.5-free）
$PY $OC --dir /path/to/project -m opencode/mimo-v2.5-free --file /tmp/screenshot.png --prompt "按这张截图改样式"

# 额度打满自救：自动轮换免费模型
$PY $OC --dir /path/to/project --title "批量改造" --prompt-file /tmp/task.md --auto-retry-quota
```

返回结构化 JSON：`ok / session_id / text / tools / tokens / cost / files_changed / permission_denied / failed_tools / attempts / quota_exhausted / next_step / log_file`。**先看 `ok`**——有工具被拒/出错、超时、限流一律 `false`。

参数：`--dir`（必填沙箱边界）`--prompt|--prompt-file` `-m/--model` `--agent build|plan` `--title` `--reuse` `--session` `--bg` `--status` `--list` `--timeout`(默认900) `--allow-unsafe-dir`(慎用) `--allow-path`(可多次) `--no-hardening`(慎用) `--allow-commit` `--auto-retry-quota` `--file`(可多次,多模态) `--attach`(serve)。

### 模型路由（免费优先，2026-08-29 实测 · 名单每周滚动）

> ⚠️ **默认做法是「不指定 `-m`」**，交给 `oc_run.py` 的默认模型 + 自动 failover。
> 免费池变动极快：08-10 的默认 `deepseek-v4-flash-free` 已挂死，08-13 的默认 `laguna-s-2.1-free` 已下架。
> **每季或遇到"又超时了"先跑一次串行探活**（`references/models.md` 有纪律与脚本）。探活务必串行，并行会假性全挂。

| 模型 | 往返耗时 | 定位 |
|------|---------|------|
| `opencode/nemotron-3.5-lightning-free` | 9.7s | **当前 `DEFAULT_MODEL`**，稳定、写文件验证通过 |
| `opencode/ling-3.0-flash-fin-free` | 6.3s | 本轮最快（新）；注意与已下架的 `ling-3.0-flash-free` 只差 `-fin` |
| `opencode/hy3-free` | 4.9s | 08-13 曾挂死、08-29 复活 |
| `opencode/mimo-v2.5-free` | 10.5s | **免费唯一多模态**，看图任务指定它 |
| `opencode/nemotron-3-ultra-free` | 31.1s | 大模型，复杂推理/长文 |
| `opencode/big-pickle` | ~60s | 未标 `free`，兜底最后一位 |
| `xiaomi/mimo-v2.5(-pro)` | — | **付费**（小海送的额度不多），免费搞定不了才换，重活前先口头确认 |

已不可用（在 `DEAD_MODELS` 里，指定会被自动替换）：`deepseek-v4-flash-free`（挂死）、`laguna-s-2.1-free`（下架）、`muse-spark-1.2-contributor-free`（地区限制）、`longcat-2.0-free`、`ling-3.0-tiny-free`、`ling-3.0-flash-free`、`north-mini-code-free`。

### 安全红线 + 权限处理（headless 自动拒绝，必读）

**实测真相**：headless `opencode run` 下 bash **零批准直接执行**（默认 `*:allow`），且任何需批准的权限**不是卡住，而是被自动拒绝**（stderr 打 `! permission requested: X; auto-rejecting`，工具 `status=error`，会"假装跑完"）。所以：

1. **`--dir` 必须是具体项目目录**，脚本内置黑名单拒跑 `$HOME`/`Desktop`/`Downloads`/`~/.ssh`/`~/.config` 等（突破需 `--allow-unsafe-dir` 且先征得同意）。
2. **跑前快照 + 跑后审 diff**（git HEAD/文件指纹 → `files_changed`）。
3. **权限三档**：① 自动拒绝→脚本如实上报 `permission_denied`/`failed_tools` 并判 `ok=false`；② 合法越界用 `--allow-path` 生成**仅本次生效**临时配置（不污染全局 `~/.config/opencode/opencode.jsonc`）；③ 默认**高危命令硬拦截**（deny `rm/git push/git reset --hard/sudo/curl|sh` 等，`--no-hardening`/`--allow-commit` 可调）。**绝不用 `--auto` 绕过**（放开整个沙箱）。
4. **成败判定硬化**：`ok = 非硬错 ∧ 非超时 ∧ 非限流 ∧ (有文本产出 ∨ (磁盘有改动 ∧ 无工具失败))`——防"表面成功活没干成"。

### 额度耗尽应对链（按公网 IP 限流）

免费额度按**公网速 IP** 限流（社区实证+官方口径）。`opencode run` 的 **exit code 永远 0**，只能靠事件流文本判（`detect_quota` 抓 429/限流）：

1. **`--auto-retry-quota`** 自动轮换其它免费模型（不同后端，换源命中率高）。
2. **换公网 IP**（小海未登录 zen，换 IP 即可续白嫖）：`scripts/oc_ip.py show` → 切飞行模式/重拨/换热点 → `oc_ip.py verify` → `oc_ip.py probe` 实测恢复。注意：**换 IP 只能网络层**（5G 重连/宽带重拨/全局 tun VPN）——因为 opencode 走直连、`oc_run.py` 已剥离代理，HTTP 代理本就不参与 zen 调用，靠代理换 IP 无效。
3. **换 `xiaomi/*` 付费**（先问，额度少）。
4. **降级**到 delegate-center 通道B（网页 AI）/ 主 Agent 自收尾。额度按"每次请求"计费，别把任务拆太碎。

---

## 通道 B：网页版免费 AI（出文本/文案/长内容）

> 详见 `references/providers.md`（DeepSeek/豆包/千问 站点结构、选择器、登录、扩展）、`references/operations.md`（CDP 操作片段/坑/故障排查）。

**执行层依赖 `web-access` skill**（CDP 直连本地 Chrome debug，端口 9222）。开始前加载 web-access 并遵循其全部指引。各 AI 站点需登录，登录态在 Chrome debug profile，与日常 Chrome 互不相通。

### 会话命名与复用（关键纪律）

同一任务**复用同一对话**（云端保留上下文，换对话丢上下文）。第一次调用用「任务名」作首条消息让系统自动起标题；后续按任务名在侧边栏搜索进入续聊；必须换新对话时加角标 `任务名-续2`。

### AI 路由

| 任务 | 首选 | 备选 |
|------|------|------|
| 代码/强推理/复杂逻辑 | DeepSeek | 千问 |
| 中文文案/创作/营销 | 豆包 | 千问/DeepSeek |
| 图像识别/OCR/看图 | 豆包（视觉强） | 千问（仅文本） |
| 翻译/总结/结构化 | 三者皆可 | — |
| 多模型交叉评审 | A 生成→B/C 评审 | — |

用户指定则遵从。未登录→请用户在该浏览器手动登录后继续，不绕过。

### 执行步骤（浏览器）

1. 加载 web-access，启动 CDP Proxy；开对应 AI 后台 tab。
2. `/eval` 探测输入框（核心：**输入框拿到了吗**）；未登录→请用户登录。
3. 写提示词（React 受控组件用原生 setter+input 事件，见 operations.md）；图像类先传附件再问。
4. 触发发送（优先点发送按钮，兜底 Enter）。
5. 轮询「停止生成」消失 + 末条回复长度两次不变（≥2.5s，上限 5min）。
6. 提取：优先「复制」按钮 + `clipboard.readText()`；兜底 DOM 提取末条消息容器。
7. 优化：模式 A 同会话追问；模式 B 多模型交叉（A 生成→B 严格审稿人评审重写→主 Agent 合并）。

### 优化路径

- **模式 A 同会话追问**（默认）：取回初稿后发优化提示词（"更口语化/更有冲击力/压缩到 X 字/补数据"），多轮收敛。
- **模式 B 多模型交叉**（提质量）：把初稿+评审要求发给另一 AI 当严格审稿人，主 Agent 对照成功标准合并 A、B 版本。只在 A 有明显改进空间时用。

### 质检（必须）

网页免费 AI 输出参差，**未经检查绝不原样交付**：对照成功标准过内容/事实/格式；问题走模式 A/B 或主 Agent 直接修。

---

## 端口地图 / Ports（2026-08-13 实测）

| 端口 / 端点 | 是什么 | 备注 |
|------|------|------|
| zen：`https://opencode.ai/zen`（**HTTPS 443**） | opencode 免费模型的实际后端 | `opencode run` 连它；**无本地端口**。"挂死"是连它不通，不是本地端口问题 |
| `opencode serve --port N` | 常驻 headless server（默认 `0`=随机，可指定 4096） | localhost；HTTP API：`GET /doc` 看 OpenAPI（`POST /session/:id/message`、`GET /session/:id/diff` 等）。批量任务冷启动加速用 |
| `opencode attach <url>` | 挂到运行中的 server | 一般接 `serve` 起的服务；挂桌面 App 的 `web` 服务对执行新提示支持不完整，**不作主路径** |
| OpenCode 桌面 App 本地 web 服务 | 实测在 **4199**（端口可能变） | 查：`lsof -nP -iTCP -sTCP:LISTEN \| grep opencode`。localhost 可达，但依赖 App 在跑 |
| 沙箱代理 `127.0.0.1:64969` | WorkBuddy Bash 注入的死代理 | **到不了 zen**；`oc_run.py` 已自动剥离，不要再手动纠缠它 |
| 通道 B 浏览器 CDP | Chrome debug **9222** / 签到 **9333** | 与 opencode 无关，属网页 AI 通道 |

> 一句话：`opencode run` 不需要你管任何端口，它自己走 443 连 zen；你只管 `--dir` 沙箱边界。`oc_run.py` 已修好代理，直接调就完事。

## 超时 / 挂死 自救决策树（遇事按序查，别只回"超时"）

1. **先看返回字段**：`oc_run.py` 输出 `ok / timed_out / quota / permission_denied / failed_tools`。`ok=false` 但 `text` 非空 ≠ 成功（可能工具失败）。
2. **`timed_out:true` 且零事件**：第一嫌疑是**代理**（现已自动剥离；若仍中招=环境变了）→ 手动 `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy` 再跑，并 `curl -m 8 -sS -o /dev/null -w "%{http_code}" https://opencode.ai/` 验证是否 200。
3. **`quota:true` / 出现 429**：公网 IP 限流 → 加 `--auto-retry-quota` 自动轮换免费模型；仍满 → `scripts/oc_ip.py` 换 IP（网络层）或换 `xiaomi/*` 付费（先问小海）。
4. **某模型零事件挂死**（非代理、非限流）：该模型静默挂死（旧 `north-mini-code-free` 即此）。脚本现已自动处理：首字节 45s 判死 → 自动 failover 换模型。若反复挂死 → 把它加进 `oc_run.py` 的 `DEAD_MODELS` 黑名单，并在 `models.md`/`experience.md` 记一笔。
5. **看图任务卡**：确认提示词在 `--file` **之前**（顺序反了会报 `File not found: <提示词>`）；`mimo` 视觉偶发被限流 → 重试 / `--auto-retry-quota` / 退化到 `xiaomi/mimo-v2.5`（付费）视觉。
6. **`failed_tools` / `permission_denied` 非空**：按上文权限三档处理（精确放行 / 高危拦截），**别信 `text`**。

## 标准外包 SOP（2026-08-14 实战验证，照做即可）

> 主 Agent 只做 5 件事：**拆任务 → 写需求书 → 后台构建 → 验收 → 复用追问**，重活全甩 opencode。

1. **拆任务**：把大活拆成 opencode 一次能搞定的粒度（一个明确目标 + 可验证成功标准）。
2. **写需求书**（`--prompt-file /tmp/task.md`）：写清「项目结构 / 功能规格 / 测试要求 / 边界约束」四段；末尾必须带边界段（禁 `git commit/push`、禁 `rm`、只在当前目录工作）。
3. **后台构建**：`--dir <项目> --title <任务名> --prompt-file /tmp/task.md --bg` 拿 `run_id`。**别死等轮询**，过 1~2 分钟再 `--status <run_id>` 查。
4. **验收**（必做，别信 `text`）：`git -C <项目> status/diff` 审 `files_changed` 是否都在允许范围 → 自己跑测试 → 看 `ok` 字段（有 `failed_tools`/`permission_denied` 一律算失败）。
5. **复用追问**：不合格用 `--reuse --title <同任务名> --prompt "精确指出第 N 处 bug"`。**复用省 token**（实测 input 93032→3262，省 ~28 倍）。⚠️ 被 SIGKILL 过的 session 别复用（会卡死），改新 session。

关键参数速查：`--agent plan`(只读分析) · `--bg/--status`(后台) · `--reuse`(会话复用) · `--allow-path`(越界精确放行) · `--file` + `-m opencode/mimo-v2.5-free`(看图) · `--auto-retry-quota`(额度轮换)。

## 共享纪律（两个通道都适用）

- **省 token 是核心**：重活外包，主 Agent 只拆任务、定标准、验收。
- **质检必做**：A 通道 `git diff` 逐项审 + 跑测试；B 通道对照成功标准核内容。
- **安全**：A 通道守 `--dir` 沙箱 + 快照 + 硬拦截；B 通道守登录态（Chrome 9222）+ 不绕过登录墙。
- **回滚**：A 通道 `git checkout`/`git stash`（尽量只在 git 仓库外包）；B 通道产物需人工搬运，无本地风险。

## References

| 文件 | 何时加载 |
|------|---------|
| `references/models.md` | A 通道选模型/多模态/额度变动 |
| `references/patterns.md` | A 通道提示词剧本/CLI/权限机制详解 |
| `references/experience.md` | A 通道踩坑经验（权限 auto-reject、额度链、参数顺序等） |
| `references/providers.md` | B 通道选 AI / 站点结构/选择器/登录/扩展新 AI |
| `references/operations.md` | B 通道 CDP 操作片段/已知坑/故障排查/过验证码 |
| `scripts/oc_run.py` | A 通道主力入口（所有本地外包走它） |
| `scripts/oc_ip.py` | A 通道出口 IP 查询/换 IP 引导/验证 |
| `scripts/oc_probe.sh` | A 通道模型探活测速 |
| `scripts/cdp_*.mjs` `db_send_precise.js` | B 通道 CDP 操作（真实键鼠/文件/裁剪） |
