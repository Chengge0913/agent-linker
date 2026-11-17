# Agent Linker

把任务派给**任意本地 agent**（通过 CLI；GUI 需要额外桥接），用一条 SQLite 队列做唯一事实来源：主控发布工单 → 执行端认领 → 回写报告与状态（`delivered` / `blocked`）。配套侧栏进度、事件流、中断恢复和验收清单。

跨进程、跨终端：哪个 agent 在哪个窗口、哪个终端跑都不影响，队列是共享的。

## 特性

- **任意 agent**：内置 DSH / CodeBuddy(WB) adapter；零代码命令模板可接任意 CLI；也可自己写 adapter
- **共享队列**：SQLite 单文件，主控与执行端可以分布在不同进程、不同终端
- **可观测**：本地只读协作日志页面、悬浮窗、进度窗，以及每项任务的事件流与报告
- **协作分组**：并行任务共用 group ID，页面集中显示协作记录
- **悬浮窗**：默认位于屏幕上方居中，可拖到四边，显示进度及任务派发和返回动画
- **可恢复**：`running` 卡住时保守标记 `blocked`，不自动重试、不自动杀进程
- **最小依赖**：Python 标准库；队列无需数据库服务；调用云端模型需要网络和对应账号
- **权限可控**：默认只读；可写权限需要显式授权并记录

## 环境要求

- Python 3.10+（仅标准库；进度窗需要 tkinter，属于可选功能）
- 至少一个执行端：
  - DSH（DeepSeek Harness）headless
  - WB / CodeBuddy CLI
  - 任意能命令行调用的 agent（通过 `src/workers/universal.py`）
  - 内置 `echo` 只用于联通测试

已在 Windows 10/11 + Python 3.14 实际跑通；Linux/macOS 未验证（脚本不依赖 Windows 专有 API，但默认路径需要调整）。

## 安装

作为 skill 安装（让 agent 能自动读到）：

```bash
git clone <repo-url> agent-linker
# Codex
cp -r agent-linker ~/.codex/skills/agent-linker
# Claude Code 或类似的 skill loader
cp -r agent-linker .claude/skills/agent-linker
```

`SKILL.md` 是入口文件；`agents/openai.yaml` 允许隐式触发；是否触发取决于当前会话指令、任务和 Codex 的判断。

不装 skill 也能用：直接在仓库目录运行下面的命令。

## 快速开始（不需要任何外部 agent）

```bash
python src/queue.py --db demo.sqlite publish echo examples/task-template.md
python src/workers/echo.py --db demo.sqlite --output-dir out
python src/queue.py --db demo.sqlite status
```

看到 `delivered` 就说明队列、执行端、报告校验整条链路是通的。

## 接入真实执行端

### DSH（DeepSeek Harness，headless）

```bash
python src/queue.py --db queue.sqlite publish dsh my-brief.md
python src/workers/dsh.py --db queue.sqlite --output-dir out \
  --patch <overlay.yml> \
  --cli "<path to DeepSeek Harness.exe>" \
  --entrypoint "<path to dsh-desktop-host/lib/cli.js>" \
  --model-label <label> --cwd "<working directory>" --timeout 300
```

DSH 的 headless profile 默认禁用桌面凭据，需要一个 overlay 启用 `credentials` / `authorization` / `deepseek-account` 组件并配置模型路由。overlay 的内容取决于你的机器与服务，请自行准备，**不要提交任何密钥**。

`--cli` 默认取环境变量 `DSH_CLI`，`--entrypoint` 默认从 `--cli` 所在目录推导（`resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js`），本机还会尝试 `D:/Deepseek Harness/DeepSeek Harness.exe`；其他安装位置可设置 `DSH_CLI` 或传 `--cli`。可识别的 overlay 模型路由显示为配置值，不能证明服务端实际模型。

### WB / CodeBuddy CLI

```bash
python src/queue.py --db queue.sqlite publish wb my-brief.md
python src/workers/wb.py --db queue.sqlite --output-dir out \
  --cli "<path to codebuddy.exe>" --model "<model>"
```

### 任意 CLI（零代码）

```bash
python src/queue.py --db queue.sqlite publish claude-code my-brief.md
python src/workers/universal.py --db queue.sqlite --output-dir out \
  --agent claude-code --command "claude -p {prompt}" --format auto
```

`--format auto` 自动识别 JSONL 标准事件；纯文本输出整段作为报告（需含 `TASK_ID` 与一行 `TASK_STATUS=COMPLETED|BLOCKED`）。

## 观察、恢复与验收

```bash
python src/progress.py --snapshot --db queue.sqlite --output-dir out
python src/sidebar.py --db queue.sqlite --output-dir out --hud
python src/recover.py --db queue.sqlite --output-dir out --worker dsh <task-id>
```

`runtime` 系 worker（`dsh` / `echo` / `src/workers/universal.py`）支持 `--open-sidebar`，WB 也支持该参数。`sidebar.py --hud` 启动或复用悬浮窗；页面使用同一队列与结果目录。

## 目录结构

```text
agent-linker/
├── README.md            # 本文件
├── SKILL.md             # skill 入口（agent 读这个）
├── LICENSE              # MIT
├── docs/                # 契约与冒烟测试
│   ├── AGENT-CONTRACT.md
│   └── smoke-test.md
├── src/                 # 全部可执行代码
│   ├── queue.py         # 队列唯一入口
│   ├── runtime.py       # 通用 runner + Adapter 基类
│   ├── workers/         # 各执行端 adapter
│   │   ├── dsh.py
│   │   ├── wb.py
│   │   ├── echo.py
│   │   └── universal.py
│   ├── dashboard.py / dashboard.html / dashboard_supervisor.py
│   ├── hud.py / assets/  # 悬浮窗和图标
│   ├── progress.py / timeline.py
│   ├── sidebar.py       # 启动侧栏并打开浏览器
│   └── recover.py       # 中断恢复
├── tests/               # 单元/集成测试
├── examples/            # 任务工单模板
└── agents/              # skill 元数据
```

## 结构

| 文件 | 作用 |
|---|---|
| `src/queue.py` | 队列唯一入口：publish / claim / finish / status，状态机 `pending → running → delivered\|blocked` |
| `src/runtime.py` | 通用 runner：`Adapter` 基类、标准 JSONL 事件、报告校验、超时、状态回写 |
| `src/workers/dsh.py` | DSH adapter（headless，默认只读） |
| `src/workers/wb.py` | WB / CodeBuddy adapter |
| `src/workers/echo.py` | 零依赖参考 adapter 与自测端（`--fail` 验证工具错误 → blocked） |
| `src/workers/universal.py` | 零代码接入任意 CLI：`--agent` + `--command` 模板 |
| `src/sidebar.py` | 启动/复用侧栏 supervisor 并打开浏览器 |
| `src/dashboard*.py` / `dashboard.html` | 127.0.0.1 随机 token 只读侧栏 |
| `progress.py` / `timeline.py` | 进度窗与事件流 → 公开说明 |
| `src/recover.py` | 中断恢复（`--worker <id>`，保守流程） |
| `docs/AGENT-CONTRACT.md` | 便携契约：任何 agent 接入的最小规则 |
| `SKILL.md` | skill 入口 |
| `agents/openai.yaml` | skill 元数据（允许隐式触发） |
| `examples/task-template.md` | 任务工单模板 |

## 接入一个新 agent

1. **零代码**（CLI 能跑就行）：用 `src/workers/universal.py`，见上文。
2. **写 adapter**（需要定制 prompt / 命令 / 事件解析）：继承 `runtime.Adapter`，实现 `name`、`display_name`、`build_prompt`、`build_command`，必要时覆盖 `parse_event` / `extract_report`。参考 `src/workers/echo.py`（最短）或 `src/workers/dsh.py`（真实）。

契约细节见 [`docs/AGENT-CONTRACT.md`](docs/AGENT-CONTRACT.md)。

## 安全边界

- 默认只读；DSH 的 `--permission-mode workspace-write` 仅在用户明确授权时使用，并记录进 `process.json`。
- 执行端只做 brief 授权的事。brief 不要要求执行端自己写报告文件——只读沙箱会拒绝，判为工具错误。
- `delivered` 只代表报告与退出码合规，不代表验收通过；主控仍要核对产物与事件。
- `--model-label` 只是本地配置标签，不是服务端模型证明。
- 队列、报告和事件可能包含任务内容，放在本地并自行控制访问；`*.sqlite`、`out/`、`results/`、侧栏运行时文件已在 `.gitignore` 中排除。

## 测试

```bash
python -m unittest discover -s tests
```

覆盖队列状态机、报告校验、工具错误优先、超时、恢复、侧栏与快照等。

## 已知限制

- Codex 执行端未验证（本机额度不可用），接入方式与 DSH/WB 相同。
- GUI 型 agent（如豆包）需要额外桥接，未包含在本仓库。
- 日志页面可在浏览器中打开；能否显示在宿主侧边面板取决于宿主浏览器支持。
- 每次 worker 调用处理一项任务，不会持续监听队列。
- WB 使用 CodeBuddy CLI，DSH 使用 headless CLI，桌面端是否显示会话未验证。
- 只读/可写边界取决于执行端自身 CLI 的权限模型。

## 致谢与来源

本项目的设计思路受 [obra/superpowers](https://github.com/obra/superpowers)（作者 Jesse Vincent，MIT License）启发：先把任务边界和验收写清楚，再派给执行者，最后按证据复核。它的 `subagent-driven-development` 等 skill 展示了「任务拆解 → 派发 → 评审门」这套方法。

本项目是独立实现：队列（跨进程 SQLite）、事件协议、侧栏、恢复和报告回执校验都由本项目自行设计；没有复制 Superpowers 的文本或代码。如果后续引入其任何原文片段，会在对应文件中保留其 MIT 版权声明。

## License

[MIT](LICENSE)