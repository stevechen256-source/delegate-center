# 经验自进化区

> **每次用完本技能都来写一笔。** 只写**验证过**的事实，标日期。猜测和"应该可以"不要写进来。
> 开工前先扫一眼本文件，别重复踩已知的坑。

格式：
```
## YYYY-MM-DD · 一句话标题
- 场景：<什么任务、什么模型、什么目录>
- 发现/结论：<验证过的事实>
- 行动：<以后该怎么做>
```

---

## 2026-08-14 · ⚠️ 致命根因：opencode stdout 重定向到文件 → 静默挂死

- 场景：`oc_run.py` 调 opencode **4 个免费模型全部 45s 零事件挂死**（timed_out、out.ndjson 0 字节、stderr 空）；但裸跑 opencode 5.9s 正常返回 "PONG OK"（cost:0）。逐层二分定位。
- 发现/结论（已稳定复现，非猜测）：
  - **根因 = stdout 直接重定向到文件**。`subprocess.Popen(stdout=open(log,'wb'))` 时 opencode 零输出、零事件，60s 无任何字节；同样的命令 stdout 改成 `subprocess.PIPE` 立即 5s 返回。
  - 逐一排除无关因素：`OPENCODE_CONFIG` 注入、代理剥离、`--dir`/`--title`/`--agent` 参数、中文长 prompt —— 都不是根因（各做对照实验，均正常）。
  - 推测：opencode（Node）检测到 stdout 非 TTY 非 PIPE 时走了异常分支（或块缓冲永不 flush）。深层原因未挖，但「文件→挂死、PIPE→正常」这个事实稳定可复现。
  - 这是"调 opencode 老是超时"的**新**根因（区别于 08-13 的代理问题）：即使代理已剥、模型活着，stdout 指向文件照样挂死。
- 行动：
  1. `oc_run.py` 已修复：stdout/stderr 改走 `subprocess.PIPE` + daemon 线程 pump 到日志文件（每 chunk 实时 flush），stall 检测仍看文件大小。修复后 8.1s 返回 `ok:true`、`attempts=1`（不再轮换 4 个模型）。
  2. 教训：**opencode 的 stdout 绝不能再直接指向文件**，任何封装必须经 PIPE 中转再落盘。若将来又要改这块，先小步验证再上。

---

## 2026-08-14 · ⚠️ PIPE 读取必须用 read1 而非 read（最关键修复）

- 场景：把 stdout 改成 PIPE + pump 线程后，openode 明明在干活（文件陆续写出、pytest cache 生成），但 out.ndjson 仍 0 字节、stall 仍误判。非阻塞 read 实测：opencode stdout 是**实时分批**输出的（304B/712B/1078B 小量分批，不是块缓冲）。
- 发现/结论：`BufferedReader.read(n)` 会**阻塞到凑满 n 字节或 EOF** 才返回；opencode 每次只写几百字节，`read(65536)` 就一直干等凑满 → 数据迟迟不落盘 → 主循环看文件大小永远 0 → stall 误杀。改用 `read1(n)`（有数据即返回、不凑满）后立即恢复正常。
- 行动：
  1. `oc_run.py` pump 线程 `src.read(65536)` → `src.read1(65536)`。修复后实时捕获完整工具轨迹（bash×5/read×7/edit×2），97.7s 单次 attempt 完成（不再 4 模型轮换）。
  2. 教训：**给 subprocess stdout 做实时 pump 时，永远用 read1 不用 read**。这是今天三个坑里最隐蔽、最致命的一个（前两个修复后仍挂死的真凶）。

---

## 2026-08-14 · --reuse 会话复用省 token 实证 + SIGKILL 残留 session 卡死

- 场景：todo-cli 构建多轮追问，对比新开会话 vs `--reuse` 复用。
- 发现/结论：
  - **复用有效**：`--reuse` 查回同一 session 后，input tokens 从 93032 降到 **3262**（省 ~28 倍），耗时 97.7s → 51.4s，`reused_session:true`，且能正确记住上一轮上下文（直接修对了我指出的两个 bug）。
  - **坑**：若上一个 session 是被 `proc.kill()`（SIGKILL）强杀的，`--reuse` 恢复它时会**卡死**（opencode 进程跑 6min 零输出、CPU 仅 18s，疑似恢复损坏的 session 锁/状态）。正常结束（含 permission_denied）的 session 复用没问题。
- 行动：
  1. 多轮追问优先 `--reuse`（又快又省 token）；但**被 SIGKILL 过的 session 不要复用**，改用新 session 重跑。
  2. 判断 session 是否可复用：看上一个 run 的 `timed_out/stalled` 是否 true（true = 被 kill，弃用）；正常 ok/权限拒结束的可复用。

---

## 2026-08-14 · stall 45s 误杀复杂构建任务（已修）

- 场景：修复 stdout 后跑真实构建（todo-cli 多文件），4 个模型又轮换失败，但 out.ndjson 有真实内容、session 一路复用、文件陆续写出。
- 发现/结论：
  - 旧的 stall 逻辑「45s 内文件大小零增长 → 判挂死」**对复杂构建任务太激进**：模型在"生成长文件内容/大模型推理"时，两轮事件间隔 31~45s+ 属正常（实测 nemotron-3-ultra 生成 test_store.py 后，下一段内容 >45s 不吐事件被误杀）。
  - 真挂死 ≠ 慢但活着：真挂死是**进程启动后一个字节都没有**；慢任务是**有事件流、只是两轮间静默久**。旧逻辑用同一个 45s 阈值无法区分两者。
- 行动：
  1. `oc_run.py` stall 改两档：**首字节**仍 45s 判死；**一旦有输出**，静默间隔放宽到 `idle_stall=max(stall_limit,300)` 秒。这样真挂死仍快速 failover，慢任务不再被误杀。
  2. 判据：复杂任务给足间隔窗口（300s），靠总 `--timeout`(900s) 兜底。

---

## 2026-08-14 · 看图 failover 假成功 + 免费池波动实测

- 场景：早晨复测时 `mimo-v2.5-free` 偶发 45s 静默挂死；早期 failover 把它换成了非多模态的 `nemotron-3.5-lightning-free`，后者回 "I cannot read images as this model doesn't support image input"，却被 `oc_run.py` 判成 `ok:true` → **无声假成功**，主 Agent 会被骗。
- 发现/结论：
  - 看图任务不能 failover 降级到非多模态模型——否则"假成功"比"挂死"更危险。
  - 免费模型池波动极大：同一模型上午 4s、下午 240s 挂死都正常；`laguna` 45s 挂死时 `lightning` 4s 救场（failover 价值在此）。
  - 偶发 CLI 打印 `Model service internal error`（opencode ai-sdk runtime 调 zen 时服务端偶发 5xx）；裸 `curl` POST `opencode.ai/zen/v1/chat/completions` 反而 5.7s 稳返 → 通道 C（zen curl 直调）是真实可靠的降级，不是摆设。
- 行动：
  1. `oc_run.py`：`--file` 多模态请求时，failover 只在同一多模态模型（`opencode/mimo-v2.5-free`）内重试，绝不降级到非多模态；mimo 挂则明确 `ok:false`。
  2. 主 Agent 收 `ok:false` + `--file` 任务 → 重试（mimo 恢复）或走付费 `xiaomi/mimo-v2.5`（先问小海）。
  3. 通道 C（zen curl 直调）在 CLI 连续失败时优先用，比 CLI 更稳更快。

---

## 2026-08-13 · ⚠️ 修正旧结论：opencode 超时真因是沙箱代理（非"忽略代理"）

- 场景：WorkBuddy Bash 调 `opencode run` 反复超时，小海手动用没问题。
- 发现（推翻 2026-08-10「opencode 不认 HTTP_PROXY」旧结论）：
  - Bash 沙箱注入 `HTTP_PROXY/HTTPS_PROXY/http_proxy/https_proxy = http://127.0.0.1:64969`（沙箱代理）。
  - **opencode CLI 会沿用这些代理变量**，而该代理到不了 `opencode.ai/zen`（curl 走代理→000；直连→200），于是 `opencode run` 卡在连 zen → 900s 超时。
  - 小海手动没问题，是因为 OpenCode 桌面 App 是普通 macOS 应用、不走该代理、直连 zen 正常。
- 行动：
  1. `oc_run.py` 已在启动 opencode 前**自动剥离四个代理变量** → 现在直接调即可（实测 5~11s、`cost:0`），无需手动 `env -u`、无需 `dangerouslyDisableSandbox`。
  2. 修正 SKILL.md / oc_ip.py / 本文件的"不认代理"旧说法；补「端口地图」「超时自救决策树」。
  3. 图片输入：`-m opencode/mimo-v2.5-free --file <图>`，**提示词必须在 `--file` 之前**，实测看图 11s 返回 "A solid red square."（cost:0）。

---

## 2026-08-10 · 技能建立时的实测基线

- 场景：opencode v1.18.14 / macOS / 免费模型全量探活 + 真实写文件任务。
- 发现：
  - `opencode/*-free` 共 8 个，**全部存活**，`--format json` 里 `cost: 0`，确认零成本。
  - headless `opencode run` 下 **bash 工具零批准直接执行**（`echo X > f.txt` 直接跑通）。默认权限 `*: allow`，只有 `doom_loop` 和 `external_directory` 是 `ask`、`question` 是 `deny`。
  - `--dir` 生效，可从任意 cwd 调用，文件确实落在目标目录。
  - 会话链路可用：`--title` 打标 → `session list --format json` 按 title+directory 查回 id → `-s <id>` 续跑，**上下文保留已验证**（让它记 ZEBRA42，下一轮准确复述）。
  - 事件流四种类型：`step_start` / `tool_use` / `text` / `step_finish`；`tool_use.part.tool` 是工具名，`step_finish.part.cost/tokens` 是计费。
- 行动：安全模型就建立在「`--dir` 是唯一硬边界 + 跑前快照 + 跑后审 diff」上；提示词必带边界段（尤其禁 git commit）。

## 2026-08-10 · 后台 worker 的 stdin 坑

- 场景：`oc_run.py --bg` 首次实现，worker 立即崩溃。
- 发现：`subprocess.Popen(..., start_new_session=True)` 只重定向 stdout/stderr 而不管 stdin，子进程会拿到失效的 fd，Python 直接
  `Fatal Python error: init_sys_streams: can't initialize sys standard streams / OSError: [Errno 9] Bad file descriptor`。
- 行动：detached 子进程**必须**带 `stdin=subprocess.DEVNULL`。已修进 `oc_run.py`（同时给 opencode 子进程也加上，防它等 stdin）。
  `--status` 也补了检测：meta 没落盘但 worker.log 有内容 → 报 `worker_failed` 并回传日志，不再假装 running。

## 2026-08-10 · ⚠️ `north-mini-code-free` 静默挂死（重要）

- 场景：探活（6min+ 无返回）+ 真实任务（3min 事件流零输出、无任何 stderr）。
- 发现：**模型出现在 `opencode models` 列表里 ≠ 它能用**。这个模型名字最像"代码专用"，实际调用完全无响应、不报错、不超时，只是干挂着。
- 行动：
  1. 已从首选降级为**拉黑**，编码任务默认改用 `deepseek-v4-flash-free`。
  2. `oc_run.py --status` 增加自动预警：跑够 120s 且事件流零输出 → 提示 `pkill -f "<模型名>"` 换模型。
  3. 通用判据：**2 分钟零事件 = 换模型**，别干等。

## 2026-08-10 · 首次真实外包成功（补单元测试）

- 场景：`/tmp/oc_proj`（git 仓库，calc.py 三个函数），`deepseek-v4-flash-free`，后台模式，提示词按 SKILL.md 第五节模板写（含边界段）。
- 发现：
  - 耗时 **215s**，`cost: 0`，工具轨迹 `read calc.py → write test_calc.py → bash pytest`。
  - **边界守住了**：`files_changed` 只有 `?? test_calc.py`，calc.py 没被动，没有偷偷 commit（git log 仍只有 init）。
  - 产出质量合格：8 个用例，含 `pytest.raises(ZeroDivisionError)` 除零分支，实跑 `8 passed`。
  - 它**会自己跑测试验证**（bash 调 pytest），自述内容与实际相符。
- 行动：这套「模板化提示词 + 边界段 + git 快照 + 跑完审 diff」的流程有效，作为标准姿势固化进 SKILL.md。
  但**仍必须自己复核**——这次它说的是真的，不代表下次也是。

## 2026-08-10 · 权限机制实测修正（auto-reject，不是 hang）

- 场景：分别测了「rm 越界」「读 --dir 外文件」在 headless `opencode run` 下的行为。
- 发现（推翻旧认知）：**headless 下权限请求不会被卡住，而是被自动拒绝**。stderr 打 `! permission requested: external_directory (/tmp/*); auto-rejecting`，被拒工具在事件流里 `status=error` 且 error 文案 `The user rejected permission to use this specific tool call.`。所以旧 SKILL.md 写的"ask → 卡住/失败"是错的——实际是直接拒，且会"假装跑完"。
- 行动：① SKILL.md / patterns.md 改为如实描述 auto-reject；② `oc_run.py` 的成败判定对"有工具执行失败/权限被拒"一律判 `ok=false`（防"表面成功活没干成"）；③ 真要越界读就读，用 `--allow-path` 生成临时配置精确放行，而非动全局或开 `--auto`。

## 2026-08-10 · 权限处理三档 + 临时配置不污染全局（均实测）

- 场景：验证 `build_perm_config` + `OPENCODE_CONFIG` 注入方案。
- 发现/结论：
  - **默认 hardening 生效**：`rm -f /tmp/...`（越界）被拒，sentinel 文件完好；`--allow-path /tmp` 下能正常读出 `--dir` 外的文件；全局 `~/.config/opencode/opencode.jsonc` 全程未被改写。
  - **deny 连 `--auto` 都突破不了**（上一轮已验）：临时配置里的 deny 规则是可信的最后一道闸。
  - 权限自动拒绝提示走 **stderr 而非 ndjson stdout**——`parse_events` 原本只扫 stdout，导致 `permission_denied` 漏报；已加 `parse_perm_lines()` 兼扫 stderr 修掉。
- 行动：三档策略固化进 SKILL.md 第七节（自动拒绝上报 / 精确放行 / 高危拦截）。

## 2026-08-10 · 成败判定躲过一个"假成功"坑

- 场景：并行跑两个任务（A=rm 越界，C=正常写文件）在**同一目录**，一度误报 A 的 `ok=true`。
- 发现：A 的 `rm` 被拒、零产出，但 `files_changed` 里出现了 C 创建的 `result.txt`（共享目录 + 并行导致），旧 `final_ok = (ok or changed)` 公式被这个**无关改动**抬成了"成功"。
- 行动：改成 `final_ok = (not hard_fail) and (not timed_out) and (not quota) and (ok or (changed and not failed_tools))`——**只要有工具失败，磁盘改动也不能算成功**。教训：跨任务别复用同一工作目录，且成败判定不能轻信 `files_changed`。

## 2026-08-10 · 额度耗尽应对链（按公网 IP 限流）落地

- 场景：小海说"免费额度用完 → 换公网 IP 即可续白嫖（没登录 zen）"，让我举一反三。
- 发现（实测硬事实）：
  - `opencode run` 的 **exit code 永远是 0**，只能靠事件流文本判限流 → 加 `detect_quota()` 抓 429/quota 特征。
  - ~~opencode **不认 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量**~~ **（此结论已推翻，见 2026-08-13 条目）**：实际是 opencode **会沿用**代理变量，而沙箱代理到不了 zen 才导致超时；换 IP 仍只走网络层（5G 重连/宽带重拨/全局 tun VPN）。
  - 免费额度按 **公网 IP** 限流，且按"每次 API 请求"计费 → 任务别拆太碎（subagent 越多请求越多越易触发）。
- 行动：应对链固化进 SKILL.md 第八节 + `oc_run.py`：`--auto-retry-quota` 自动轮换免费模型（单测验证 swap 生效）；仍满则在 `next_step` 写全指引（换 IP 用 `oc_ip.py` / 换 xiaomi 付费先问 / 降级到 delegate-center 通道B 即原 ai-web-delegate）；`oc_ip.py` 已支持 show/wait/verify/probe。

## 2026-08-10 · 多模态 `--file` 参数顺序坑（已修）

- 场景：用 `mimo-v2.5-free` 读图，`oc_run.py --file <图>`。第一版把 prompt 放在 `-f` 之后，结果 opencode 报 `File not found: <提示词>` ——它把 `-f` 之后的位置参数全当文件路径吞了，提示词被当成要读的文件。
- 发现：`opencode run` 的约定是**提示词（positional message）必须放在 `-f/--file` 之前**。
- 行动：已调整 `build_cmd`，prompt 提到 `-f` 前面（顺序：`run ... -m <模型> [session/title] <提示词> [-f 文件...]`）。重测 `mimo-v2.5-free --file 红黑渐变图` → 正确输出"A horizontal gradient transitioning from black on the left to red on the right"，`ok=true, cost=0`。多模态任务走 `--file`（可多次），不是 `--attach`（`--attach` 是连 serve 用的）。

## 2026-08-10 · 模型新增事实（小海补充）

- `opencode/mimo-v2.5-free` 是**免费里唯一的多模态**，能读图片 → 看图/截图类任务指定它（如按截图改样式、识别图中代码）。
- 小海已导入 **xiaomi API**（自己的 key，送的额度、不多）。已**预授权可试用**，但默认仍走免费模型；重活/批量换 xiaomi 前先口头确认，别烧光礼物额度。已更新 models.md / SKILL.md 路由规则。
- 2026-08-10 实测 `xiaomi/mimo-v2.5` 冒烟通过：返回 `OK`，`cost≈0.000913`（按 token 计费，极省）。key 有效、链路通，需要更强推理/多模态不够时可用它兜底。

<!-- 新经验往上面追加，保持倒序或按日期分组都行，能查到就好 -->

- 场景：同一 title 追问补用例，`laguna-s-2.1-free`。
- 发现：
  - `--reuse` 生效：查回同一 `session_id`，上下文在（它知道"再追加"指哪个文件），**16~29s 就搞定**（比新开会话的 215s 快一个量级）。
  - **bug**：首轮 `files_changed` 空报。原因是 git 模式只对比 `git status --porcelain` 的行集合，而 `?? test_calc.py` 这种**跑之前就已 dirty/untracked** 的文件被再次修改时，status 行一字不变 → 判为无变化。
  - 修复：快照时对已 dirty/untracked 的文件额外记 md5，diff 时补检内容变化。修复后正确报出 `M  test_calc.py`。
- 行动：**多轮追问一律走 `--reuse`**，又快又省。审 diff 时以 `git diff` 为准，`files_changed` 只当索引。

---

<!-- 新经验往上面追加，保持倒序或按日期分组都行，能查到就好 -->
