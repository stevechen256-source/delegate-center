# 提示词剧本 · CLI 细节 · 权限机制

## 一、提示词剧本库（复制改用）

所有剧本都必须带「边界」和「验收」两段——opencode 是能动手的 agent，不给边界它就自由发挥。

### 1. 补单元测试

```text
## 任务
为 <目录/文件> 中的 <模块> 编写单元测试。

## 上下文
技术栈：<语言 + 测试框架>。现有测试放在 <路径>，命名约定 <约定>。
被测代码要点：<关键函数/分支/边界>

## 具体要求
1. 新建/补充测试文件：<明确路径>
2. 覆盖：正常路径、边界值、异常分支（至少 N 个用例）
3. 遵循现有测试文件的写法与断言风格

## 边界
- 只允许新建/修改 <测试文件路径>；禁止改任何业务代码
- 禁止 git commit / push / 安装依赖 / 改配置
- 被测代码有 bug 也不要顺手修，写进最后的说明里

## 验收
- 运行 <测试命令> 全部通过
- 输出：新增了哪些用例，哪些分支没覆盖到及原因
```

### 2. 批量机械改造（改 API / 换库 / 统一风格）

```text
## 任务
把 <目录> 下所有 <文件类型> 中的 <旧写法> 替换为 <新写法>。

## 上下文
旧写法示例：
<代码块>
新写法示例：
<代码块>
共约 <N> 处，分布在 <范围>。

## 具体要求
1. 先用 grep 列出所有待改位置，输出清单
2. 逐个改，保持其余代码与格式不动
3. 无法机械替换的（语义有差异）跳过并记录

## 边界
- 只允许修改 <目录> 下的 <文件类型>；禁止碰配置、依赖、CI、测试快照
- 禁止 git commit / push
- 禁止"顺手优化"任何无关代码

## 验收
- 改完再 grep 一次旧写法，输出剩余数量及原因
- 运行 <构建/测试命令> 通过
- 输出改动清单：文件 → 改了几处
```

### 3. 独立小工具 / 脚本

```text
## 任务
写一个 <语言> 脚本 <文件名>，实现 <功能>。

## 上下文
运行环境：<版本/依赖限制>。输入：<格式/来源>。输出：<格式/去处>。

## 具体要求
1. 单文件、只用标准库（或：只用 <指定依赖>）
2. 支持命令行参数：<参数说明>
3. 错误处理：<失败时的行为>
4. 顶部写简短用法注释

## 边界
- 只允许新建 <文件名>；不动项目其它文件；不装依赖
- 禁止 git commit / push

## 验收
- 用 <示例命令> 实跑一次并贴出真实输出（不要编造）
- 输出：脚本用法 + 已知限制
```

### 4. 只读分析（`--agent plan`，零写入风险）

```text
## 任务
通读 <目录>，产出一份 <主题> 分析报告。

## 具体要求
1. 先列目录结构与关键文件
2. 分析 <关注点：架构/性能隐患/重复代码/安全问题>
3. 给出 3~5 条改进建议，每条含：问题位置（文件:行）、为什么是问题、怎么改、预估影响面

## 边界
- **只读**：禁止修改任何文件、禁止执行有副作用的命令
- 结论必须基于实际读到的代码，不许臆测；没读到的地方明说

## 输出格式
Markdown：概览 → 逐条发现（含代码位置）→ 优先级排序建议
```
配合 `--agent plan` 使用，双保险。

### 5. 生成文档

```text
## 任务
为 <项目/模块> 生成 <README / API 文档 / CHANGELOG>。

## 上下文
读取 <关键文件> 了解实际行为。目标读者：<谁>。语言：<中文/英文>。

## 具体要求
1. 内容必须来自真实代码，禁止编造不存在的参数、命令、示例
2. 结构：<章节列表>
3. 所有示例命令必须是真能跑的

## 边界
- 只允许新建/覆盖 <文档路径>；不动代码
- 禁止 git commit / push

## 验收
- 文档中每个命令/参数都能在代码里找到依据
- 输出：哪些部分是从代码确认的、哪些是推测的（推测的要标注）
```

### 6. 多轮追问修正（`--reuse`）

```text
上一轮的产出有这些问题：
1. <具体问题 1，指明文件和位置>
2. <具体问题 2>

请只修正这些点，不要重写其它已经正确的部分。改完输出改动清单。
```

## 二、CLI 参数全表（v1.18.14 实测）

```
opencode run [message..]
  -m, --model      provider/model
      --agent      build(默认,全权限) | plan(只读规划) | general | explore
  -s, --session    续跑指定会话（保上下文，实测有效）
  -c, --continue   续跑最近一次会话
      --fork       续跑时分叉（配合 -c/-s）
      --title      会话标题（用于后续按标题查回）
      --dir        工作目录（沙箱边界，可从任意 cwd 调用）
      --format     default | json（json 为 ndjson 事件流）
  -f, --file       附加文件到消息
      --attach     附着到 opencode serve，如 http://localhost:4096
      --auto       自动批准所有非 deny 的权限请求（危险，默认别开）
      --variant    推理强度（provider 相关：high/max/minimal）
      --thinking   显示思考块
      --share      分享会话（会上传，默认别用）
```

其它常用：
```bash
opencode models [provider] [--refresh]      # 列模型
opencode session list --format json -n 20   # 列会话（含 id/title/directory）
opencode export <sessionID>                 # 导出完整会话 JSON
opencode stats --days 7                     # 用量与费用
opencode serve --port 4096                  # 常驻 headless server
```

## 三、`--format json` 事件流结构（实测）

ndjson，每行一个事件，四种类型：

| type | 关键字段 | 说明 |
|------|---------|------|
| `step_start` | `sessionID` | 一个推理步开始 |
| `tool_use` | `part.tool`、`part.state.input`、`part.state.output`、`part.state.status` | 工具调用（write/read/bash/grep/glob/edit…） |
| `text` | `part.text` | 模型输出的文本 |
| `step_finish` | `part.tokens`、`part.cost`、`part.reason` | 步结束，含 token 与费用 |

所有事件都带 `sessionID` → 可用于后续 `-s` 续跑。`oc_run.py` 已封装解析。

## 四、权限机制（安全核心）

opencode 的 `permission` 配置，取值 `allow` / `ask` / `deny`：

**默认值（本机 `opencode agent list` 实测）**：
```
*                    → allow      ← 注意：包括 bash！
doom_loop            → ask        （同一工具同输入重复 3 次）
external_directory   → ask        （访问工作目录之外的路径）
question             → deny       （headless 下它问不了用户）
read: *.env          → deny
```

**结论（2026-08-10 实测修正）**：headless `opencode run` 下，**bash 命令零批准直接执行**（已实测：`echo BASHOK > from_bash.txt` 直接跑通）。所以 `--dir` 是主要边界；而越界访问（`external_directory`）和任何需批准的权限，headless 下**不是卡住，而是被自动拒绝**（stderr 打 `! permission requested: X; auto-rejecting`，工具 `status=error`）。任务会"假装跑完"——必须靠 `oc_run.py` 的 `ok`/`failed_tools`/`permission_denied` 判生死，**别信 `text`**。

**收紧的推荐做法：用临时配置，别碰全局**（默认 `oc_run.py` 已开启）。
`oc_run.py` 每次运行会按参数自动生成一份**仅本次生效**的临时 `opencode.jsonc`，通过 `OPENCODE_CONFIG` 环境变量注入，**绝不改写**小海全局 `~/.config/opencode/opencode.jsonc`：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "permission": {
    "bash": {
      "*": "allow",
      "rm *": "deny", "rmdir *": "deny",
      "git push *": "deny", "git reset --hard*": "deny", "git clean *": "deny",
      "sudo *": "deny", "shutdown *": "deny", "reboot *": "deny", "mkfs*": "deny", "dd *": "deny",
      "npm publish*": "deny", "pnpm publish*": "deny", "yarn publish*": "deny",
      "pip uninstall*": "deny", "curl * | sh": "deny", "curl * | bash": "deny",
      "wget * | sh": "deny", "wget * | bash": "deny", ":(){*": "deny",
      "chmod 777 *": "deny", "chown *": "deny",
      "git commit *": "deny"          // 除非显式 --allow-commit
    },
    "external_directory": {            // 仅当 --allow-path 指定时才加
      "/path/to/allowed/*": "allow",
      "/path/to/allowed": "allow"
    }
  }
}
```
要点：
- headless 下 `ask` 约等于"被拒"（没人应答），所以规则只用 `allow` / `deny` 两种值，别用 `ask`。
- **`deny` 连 `--auto` 都突破不了**（实测）：即便你开了 `--auto`，上面的 deny 仍生效——所以 hardening 是可信的最后一道闸。
- **精确放行越界目录用 `--allow-path <目录>`**，它只在该次临时配置里加 `external_directory: allow`，全局配置纹丝不动。
- 想自己手写全局配置（`~/.config/opencode/opencode.jsonc` 或 `~/.config/opencode/agents/<name>.md` 的 `permission:` frontmatter）也可以，但**会影响小海自己用 opencode 的体验 → 动之前先问用户**。默认走 `oc_run.py` 的临时配置就够了，无需动全局。

## 五、批量任务：常驻 server

一轮要跑 ≥3 个任务时，避免每次冷启动：

```bash
opencode serve --port 4096 &
sleep 2
python3 oc_run.py --dir /p --attach http://localhost:4096 --prompt "任务1"
python3 oc_run.py --dir /p --attach http://localhost:4096 --prompt "任务2"
# 收工
pkill -f "opencode serve --port 4096"
```
server 还提供 HTTP API（`GET /doc` 看 OpenAPI），如 `POST /session/:id/message`、`GET /session/:id/diff`。
一般用不上，CLI 够了；真要做复杂编排再查 https://opencode.ai/docs/server 。

## 六、AGENTS.md：给外包任务喂项目约定

opencode 会自动读项目根的 `AGENTS.md` 作为长期上下文。如果同一个项目要反复外包，
把项目约定（技术栈、目录结构、代码风格、禁改文件、测试命令）写进 `AGENTS.md`，
比每次都塞进提示词更省事、也更稳。

生成方式：`opencode run --dir /p "/init"`，或主 Agent 直接手写（更可控，推荐）。
⚠️ 在别人的项目里新建 AGENTS.md 前先问用户，那是会进 git 的文件。
