# Agent Linker

[简体中文](README.md) | English

Coordinate local CLI agents through a shared SQLite queue. A lead publishes a scoped brief, an addressed worker claims it, and the worker returns a report and a `delivered` or `blocked` status. A local dashboard, floating progress window, event logs, and recovery tools make the work visible and reviewable.

## Features

- Built-in adapters for DeepSeek Harness (DSH) headless and CodeBuddy CLI (WB), plus a universal command template and an adapter interface for other CLI agents.
- A shared SQLite queue for separate processes and terminals, with group IDs to associate related tasks.
- A token-protected, read-only local activity dashboard, task event streams, reports, and a desktop progress window.
- A draggable floating window that defaults to the top center and shows progress, dispatch, and return animations on all four screen edges.
- Conservative recovery of interrupted tasks after process inspection. Each worker invocation processes one task.
- Python standard-library dependencies for the queue and workers. Cloud model calls require a network connection and the relevant account.
- Read-only defaults for the supplied DSH and WB workers, with explicit options for authorized changes or commands.

## Requirements

Python 3.10 or newer is required. The floating and desktop progress windows also require tkinter. DSH, WB, or another compatible CLI is needed for real agent work. The included echo adapter can exercise the queue without an external agent.

The included setup has been exercised on Windows with Python 3.14. Linux and macOS behavior has not been verified, and local paths and GUI behavior may need adjustment.

## Install

```bash
git clone https://github.com/Chengge0913/agent-linker.git
cp -r agent-linker ~/.codex/skills/agent-linker
# Alternatively, install under .claude/skills/agent-linker for a compatible loader.
```

`SKILL.md` is the skill entry point. The Codex metadata permits implicit invocation. Whether the skill is selected depends on the current instructions, task, and Codex's judgment. Scripts can also be run directly from the repository.

## Quick start with echo

```bash
python src/queue.py --db demo.sqlite publish echo examples/task-template.md
python src/workers/echo.py --db demo.sqlite --output-dir out
python src/queue.py --db demo.sqlite status
```

A `delivered` result indicates that the queue, worker, and report validation completed. Review a real agent's report and evidence before accepting its work.

## Real agents

### DeepSeek Harness

```bash
python src/queue.py --db queue.sqlite publish dsh my-brief.md
python src/workers/dsh.py --db queue.sqlite --output-dir out \
  --patch <overlay.yml> --cli "<path to DeepSeek Harness.exe>" \
  --cwd "<working directory>" --timeout 300
```

The headless profile requires an environment-specific overlay enabling `credentials`, `authorization`, and `deepseek-account` and configuring a model route. Keep credentials and private overlays out of the repository. `--cli` defaults to `DSH_CLI`, with a fallback for the original local installation at `D:/Deepseek Harness/DeepSeek Harness.exe`. The entry point is derived from the executable unless explicitly supplied. A recognized overlay route is displayed as a configured model and provider, not proof of the serving model.

### WorkBuddy / CodeBuddy CLI

```bash
python src/queue.py --db queue.sqlite publish wb my-brief.md
python src/workers/wb.py --db queue.sqlite --output-dir out \
  --cli "<path to codebuddy.exe>" --model "<model>"
```

The default model is `glm-5.3`. Read access is requested by default. `--allow-edits` enables edit tools for authorized work. `--allow-bash-command` accepts specific approved commands. Permission behavior also depends on the CLI and its existing configuration.

### Another CLI

```bash
python src/queue.py --db queue.sqlite publish claude-code my-brief.md
python src/workers/universal.py --db queue.sqlite --output-dir out \
  --agent claude-code --command "claude -p {prompt}" --format auto
```

The universal worker accepts standard JSONL events or plain text. A final report must include the exact task ID and one `TASK_STATUS=COMPLETED` or `TASK_STATUS=BLOCKED` line. See the [agent contract](docs/AGENT-CONTRACT.md) for the full protocol.

## Monitor and recover

```bash
python src/progress.py --snapshot --db queue.sqlite --output-dir out
python src/sidebar.py --db queue.sqlite --output-dir out --hud
python src/recover.py --db queue.sqlite --output-dir out --worker dsh <task-id>
```

The sidebar command starts or reuses the local dashboard and can start the floating window with `--hud`. Use the same queue and output directory for the dashboard and workers. Related tasks can be published with the same `--group <id>`. The dashboard uses a random local URL token; keep that URL and task data private.

For a task left `running`, inspect the worker and child processes and artifacts first. After confirming they have stopped, rerun recovery with `--confirmed-stopped` to mark the task blocked. Recovery does not retry or kill processes.

## Project layout

- `SKILL.md` and `agents/openai.yaml` provide the skill instructions and metadata.
- `src/queue.py` manages publishing, claiming, finishing, and queue status.
- `src/runtime.py` and `src/workers/` provide the worker protocol and adapters.
- `src/dashboard.py`, `dashboard.html`, `dashboard_supervisor.py`, and `sidebar.py` provide the activity page.
- `src/hud.py` and `src/assets/` provide the floating window and visuals.
- `src/progress.py`, `timeline.py`, and `recover.py` provide progress, event summaries, and recovery.
- `examples/`, `docs/`, and `tests/` contain task templates, the integration contract, and automated tests.

## Boundaries and limitations

`delivered` means the worker returned an acceptable report and exit status. The lead still reviews the actual result. Tool permissions depend on each agent's CLI and configuration. DSH uses a headless session and WB uses the standalone CodeBuddy CLI. Display in their desktop apps has not been verified. GUI-only agents need an additional bridge. A dashboard page can be opened in a browser; embedding it in a host panel depends on that host.

Queue files, reports, and event logs can contain task data. Keep them local and control access. The repository ignores local databases, result directories, dashboard runtime files, and the local DSH credential overlay.

## Tests

```bash
python -m unittest discover -s tests
```

The suite covers queue transitions, reports, error handling, timeouts, recovery, dashboard snapshots, and floating-window behavior.

## Acknowledgments

The project is inspired by [obra/superpowers](https://github.com/obra/superpowers) by Jesse Vincent (MIT), particularly its emphasis on scoped tasks and evidence-based review. Agent Linker is an independent implementation.

## License

[MIT](LICENSE)
