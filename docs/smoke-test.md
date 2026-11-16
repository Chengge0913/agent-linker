# 冒烟测试

本文件说明如何在不依赖外部 agent 的情况下验证整条链路，以及真实执行端的验证清单。

## 1. 内置 echo（无需外部 agent）

```bash
python src/queue.py --db smoke.sqlite publish echo examples/task-template.md
python src/workers/echo.py --db smoke.sqlite --output-dir smoke-out
python src/queue.py --db smoke.sqlite status
```

预期：

- 任务状态 `delivered`；
- `smoke-out/<task-id>/` 下有 `events.jsonl`、`process.json`、`progress.jsonl`、`report.md`；
- 再发一个 echo 工单并用 `python src/workers/echo.py --fail ...` 运行：工具调用失败但报告自称 COMPLETED，预期被标记 `blocked`（验证「工具错误优先于自述」）。

## 2. 全量测试

```bash
python -m unittest discover
```

43 项，覆盖队列状态机、报告校验、工具错误优先、超时、恢复、侧栏与快照等。

## 3. 真实执行端验证清单

对每个执行端（DSH / WB / 其他 adapter）：

- 发布一个只读 brief，要求读取某个文件并报告实际内容；
- 检查队列最终状态为 `delivered`，`receipt` 指向报告；
- 打开 `report.md`，与源码逐条核对（行数、名称、取值）；
- 检查 `process.json` 的 `permission_mode`（DSH）与 `configured_model`；
- 检查 `events.jsonl` 的事件类型序列（init / tool / final）；
- 侧栏 `/api/tasks`、`/api/task/<id>` 能看到该任务与时间线；
- 中断场景：任务 `running` 时用 `recover.py --worker <id> <task-id>` 预检，确认进程已停后再加 `--confirmed-stopped`。

## 4. 已验证记录

2026-11-16，Windows 10/11，Python 3.14：

- DSH 只读工单交付成功（`deepseek-v4.1-flash`），报告内容与 `queue.py` 源码一致（行数、子命令、worker 取值）。
- WB 只读工单交付成功（`glm-5.3`），报告的状态机与 finish 条件与源码一致。
- 安装副本内 echo 工单交付成功，证明 skill 拷贝到其他目录后可直接运行。
- Codex 执行端未验证（本机额度不可用），接入方式与 DSH/WB 相同。

## 5. 限制

- `delivered` 表示报告格式与退出码合规，不等于验收通过；主控仍需核对证据。
- 只读/可写边界取决于执行端自身 CLI 的权限模型；DSH 的 `workspace-write` 仅用于明确授权的任务。
- 侧栏只展示执行端实际产生的公开事件，不能证明未展示的内容不存在。