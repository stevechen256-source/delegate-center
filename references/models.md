# 模型清单与路由（opencode）

> 免费模型是**滚动更新**的，会上下线。发现对不上就跑 `scripts/oc_probe.sh`，并回来更新本文件（标日期）。

## 🔴 最重要的原则：别信"首选"，信机制

**免费模型池按小时波动**——同一个模型上午 5s 秒回、下午 240s 挂死是常态（2026-08-13 当天就在 `deepseek-v4-flash-free`、`nemotron-3.5-lightning-free` 上各撞了一次）。

所以：
- ❌ **不要**凭本文件的"首选"标注硬选模型，然后挂了就报"超时放弃"。
- ✅ **不指定 `-m`**，让 `oc_run.py` 用默认模型 + 自动 failover：45s 零事件 → 判挂死 → 提前 kill → 自动换下一个（最多再试 3 个）。
- ✅ 本文件的表格只当**参考画像**（谁多模态、谁大模型），不当选型硬规则。

## 实测快照 · 2026-08-13 晚（opencode v1.18.14，本机当前）

`opencode models` 当前输出 **9 个模型（6 免费 + 3 付费）**。名单相比当日下午又滚了一轮：`longcat-2.0-free` 已下架（0.8s 快失败）。
"探活"列基于当晚连续实测：✅=可通、🟡=时好时挂、❌=挂死或下架。

| 模型 | 探活（08-13 晚） | 计费 | 定位 |
|------|------|------|------|
| `opencode/laguna-s-2.1-free` | ✅ 6.0/8.1s **当前默认** | 免费 | 快、当晚最可靠（两次 failover 都是它救场） |
| `opencode/mimo-v2.5-free` | ✅ 5.0s **唯一多模态** | 免费 | **看图**用 `--prompt ... --file <图>`（顺序不能反）；实测看图 11s |
| `opencode/big-pickle` | ✅ 4.9s | ⚠️ 未标 free | 快，但未标 free → 排轮换链最后 |
| `opencode/nemotron-3-ultra-free` | ✅ 20.9s | 免费 | 大模型，复杂推理，慢但稳 |
| `opencode/nemotron-3.5-lightning-free` | 🟡 5.7s 通过，后转 90s 挂死 ×2 | 免费 | 波动大，只作轮换备选 |
| `opencode/deepseek-v4-flash-free` | ❌ **挂死**（100s/240s 零输出） | 免费 | **已进 `DEAD_MODELS` 黑名单**：指定它会自动替换（除非 `--force-model`）。曾被旧文档标为"默认首选"→ 这是"总超时"的元凶之一 |
| `opencode/hy3-free` | ❌ **挂死**（100s 零输出） | 免费 | 同上，已黑名单 |
| `opencode/longcat-2.0-free` | ❌ 已下架（0.8s 快失败） | — | 从轮换链移除 |
| `xiaomi/mimo-v2.5` / `-pro` / `-pro-ultraspeed` | ⚠️ 存在 | 💰 付费 | 免费全挂时的兜底，**用前必须问小海** |

`--format json` 的 `step_finish` 事件里带 `cost` 字段，免费模型实测 **`cost: 0`**——每次跑完可从 `oc_run.py` 输出的 `cost` 复核，非 0 立刻停下来问用户。

## 选型规则

0. **首选做法 = 不指定 `-m`**，交给脚本默认模型 + 自动 failover。只有下面这些"能力型"需求才显式指定：
1. **默认只用 `-free`**。动 `xiaomi/*` 前必须先问小海（要花钱）。
2. **看图/读截图** → 必须 `-m opencode/mimo-v2.5-free`（唯一免费多模态），且 `--prompt` 要写在 `--file` **之前**。
3. **复杂推理/长文分析** → `-m opencode/nemotron-3-ultra-free`（慢但稳，20s 级）。
4. **黑名单**（`oc_run.py` 里 `DEAD_MODELS`）：`deepseek-v4-flash-free`、`hy3-free`、`north-mini-code-free`、`longcat-2.0-free`。指定它们会被自动替换；想验证复活加 `--force-model`。
5. 同一任务连续 2 轮跑偏就换模型，别死磕同一个。
6. **静默挂死已自动处理**：模型名在列表里 ≠ 能用。45s 零事件 → 脚本自动判死、kill、换模型（不用再手动 `pkill`）。
   若某模型反复挂死 → 把它加进 `oc_run.py` 的 `DEAD_MODELS` 并更新本文件。

## 响应时间的构成

一次 `opencode run` 总耗时 ≈ 冷启动（数秒，含 MCP/LSP 初始化）+ 模型推理 + 工具轮次。
- 纯问答：10~30s
- 单文件写入：60~90s
- 多文件改造：2~10min（用 `--bg`）

批量任务想省冷启动 → `opencode serve` + `--attach`（见 patterns.md）。

## 认证与配额

- 已认证 provider：`xiaomi`（key 存 `~/.local/share/opencode/auth.json`，别打印它）。
- `opencode/*` 免费模型走 opencode zen，实测**无需额外登录**即可用。
- 免费额度未见明确文档，若突然返回 429/额度错误 → 换另一个 free 模型，并在 experience.md 记一笔。
- 查看历史消耗：`opencode stats --days 7`。

## 更新流程

```bash
opencode models                      # 看列表变没变
scripts/oc_probe.sh                  # 只探免费模型，并行，出耗时表
scripts/oc_probe.sh --code           # 更贴近真实：让模型写个文件，验证工具调用能力
```
然后更新上表 + 在 `experience.md` 追加一条带日期的记录。

## 实测快照 · 2026-08-18 上午（opencode v1.18.14，本机当前）

> 与 08-13 的画像**正好反转**，再次印证"免费模型按小时波动、别信首选"。

| 模型 | 探活（08-18 上午） | 结论 |
|------|------|------|
| `opencode/nemotron-3.5-lightning-free` | ✅ 稳定 5~18s（T1~T4 全程用它，多次成功） | **今日首选，显式 `-m` 指定** |
| `opencode/laguna-s-2.1-free`（08-13 的"当前默认"） | ❌ 首字节 45s+ 零输出直接挂死（连挂 2 次，bg worker 卡死） | **避开**；默认值踩坑时显式 `-m nemotron-3.5-lightning-free` |

### 08-18 新增实操纪律（T2/T3/T4 派单验证）
1. **显式 `-m opencode/nemotron-3.5-lightning-free`** + 长任务 `--timeout 1200`（默认 900 会误杀 15min+ 的大活）。
2. **验收以 `node --test` 实测为准**，不信 run 的 `ok` 标志（T3 实测：ok=false 但 33/33 全绿，末段 failed_tools 是模型冗余清理没匹配上，不影响功能）。
3. **多 attempt 覆盖风险**：attempt1 成功后若被判超时 → failover 到 attempt2/3 重跑会**覆盖已有成果**（T2/T4 各踩一次）。处置：发现 `out_2/out_3.ndjson` 出现且 attempt1 已成功 → 立即 `pkill -f <run title>` 杀掉 worker，以 attempt1 成果为准，别等它重写完。
4. 池子拥堵时（直测也 45s+ 无响应）→ 杀掉重派比干等 failover 更高效（T4 首次卡 8 分钟，杀掉重派 1 分钟开工）。

---

## 实测快照 · 2026-08-29 晚（opencode CLI v1.18.14 / 桌面 App v1.18.21）

> **本次是"换代"级别变动**：`opencode/*` 免费池从 9 个缩到 7 个，**旧默认 `laguna-s-2.1-free` 已彻底下架**（调用直接返回 `UnknownError / server error`）。
> 在修掉 `oc_run.py` 的 `DEFAULT_MODEL` 之前，通道 A 每次调用必然失败 —— 这类"默认模型悄悄下架"是最该定期复核的点。

### 🔴 新增重要纪律：探活必须串行

并行探活（7 个模型同时发）会互相抢连接池，实测 **6 个假性 STALL（90s 零响应）**，只有 1 个返回。
**串行逐个测**则全部存活。→ 以后判"某模型挂死"之前，先确认不是自己并发打挂的。

### 串行实测结果（PONG 往返 + 写文件双重验证）

| 模型 | 往返 | 写文件 | 状态 | 说明 |
|------|------|--------|------|------|
| `opencode/hy3-free` | **4.9s** | ✅ | ✅ **复活** | 08-13 曾挂死进黑名单，08-29 实测恢复 → 已移出 `DEAD_MODELS` |
| `opencode/ling-3.0-flash-fin-free` | **6.3s** | ✅ | ✅ **新增** | 本次最快的新模型；注意与已下架的 `ling-3.0-flash-free` 名字只差 `-fin` |
| `opencode/nemotron-3.5-lightning-free` | 9.7s | ✅ | ✅ 稳定 | **当前 `DEFAULT_MODEL`**，08-18 有全天实战记录 |
| `opencode/mimo-v2.5-free` | 10.5s | ✅ | ✅ | **免费池唯一多模态**，看图仍走它 |
| `opencode/nemotron-3-ultra-free` | 31.1s | — | ✅ 慢但稳 | 复杂推理/长文分析 |
| `opencode/big-pickle` | ~60s | — | ✅ | ⚠️ 未标 `free`，兜底最后一位 |
| `opencode/muse-spark-1.2-contributor-free` | 4.9s | — | ❌ **不可用** | 新增但地区限制：`This model is not available in your country` |

### 已下线 / 黑名单变动

| 模型 | 变动 | 处理 |
|------|------|------|
| `opencode/laguna-s-2.1-free` | **已下架**（08-13 的默认首选） | 新增进 `DEAD_MODELS`；`DEFAULT_MODEL` 改为 `nemotron-3.5-lightning-free` |
| `opencode/muse-spark-1.2-contributor-free` | 地区限制 | 新增进 `DEAD_MODELS` |
| `opencode/hy3-free` | **复活**（4.9s 通过） | 从 `DEAD_MODELS` 移出，排轮换链第 3 |
| `opencode/deepseek-v4-flash-free` | 仍在列表外 | 保留在黑名单 |

### 端到端验证（修复后）

```
$PY $OC --dir /tmp/oc_e2e --title "e2e-验证模型修复" --prompt-file /tmp/e2e_task.md
→ attempt 1 / nemotron-3.5-lightning-free / ok: true / 22.1s / cost 0 / failed_tools 0
→ mathutil.py 正确新增 mul()，add() 未被改动
```

### 08-29 新增纪律
5. **每季度（或发现"又超时了"时）先跑一次串行探活**，核对 `DEFAULT_MODEL` 是否还在列表里 —— 默认模型下架是静默失败，报错信息（`server error`）看不出根因。
6. **探活必须串行**（见上）。
7. **新模型要测"写文件"而不只是 PONG**：能回话 ≠ 能调工具。本次 ling/hy3/nemotron 三个都补了写文件验证才敢放进轮换链。
