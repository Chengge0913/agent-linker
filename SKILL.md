---
name: agent-linker
description: Coordinate scoped local tasks between a lead and any CLI agent worker (WB CodeBuddy, DSH headless, a zero-code command template, or a custom adapter), track task state, and review returned evidence. Use when the user asks to delegate work to a local agent, add a new agent to the queue, or use the local agent queue.
---

# Local agent coordination

The lead defines a unique task with its objective, inputs, allowed file changes, dependencies, expected evidence and report. The reusable [task brief template](examples/task-template.md) separates an optional business label from the automatically generated queue ID. Preserve existing work. Delegate only work the user has authorized. Independent tasks need non-overlapping write scopes.

The design is agent-neutral: one SQLite ledger, one worker runner, one progress window and side panel, and one adapter per agent. WB and DSH are the two adapters that ship with this skill; any other CLI agent can join without changing the queue or the UI. The portable [agent worker contract](docs/AGENT-CONTRACT.md) states the minimum an agent must do — queue commands, report format and optional event protocol — and applies to every agent, not just the adapters shipped here.

## Install and use from any agent

The skill is a directory of scripts and documents; it has no network dependency and no service to start. Install the `agent-linker` directory where the agent loads skills, or keep it in the project and point the agent at this file:

- Codex: copy it under `~/.codex/skills/agent-linker/`.
- Claude Code or a similar skill loader: copy it under `.claude/skills/agent-linker/` or the loader's skills directory; `SKILL.md` is the entry point.
- DSH: install it as a skill bundle, or keep the directory in the workspace and read this file.
- Any other agent: it needs Python 3, the scripts in this directory and this document. The lead and the worker may be different agents; they only share the database path and the brief/receipt paths. The progress window and side panel are optional.

When the scripts change, update every installed copy; a stale copy is a different deployment.

## The shared queue

From the directory of these scripts, use the same absolute database path for every command:

```powershell
python src/queue.py --db <database-path> publish <agent-id> <brief-path>
python src/queue.py --db <database-path> claim <agent-id>
python src/queue.py --db <database-path> finish <agent-id> <task-id> <receipt> [--blocked]
python src/queue.py --db <database-path> status
```

`<agent-id>` is any lowercase agent name such as `wb`, `dsh`, `claude-code` or `echo` (letters, digits and hyphens, at most 32 characters). A worker claims only tasks addressed to its own id, one at a time, and can finish only a task it currently holds in `running`. States are `pending`, `running`, `delivered` and `blocked`. The ledger stores no prompt text and no model output; it holds the brief path, the state and a receipt (a report path or a blocker description).

## Worker protocol (`runtime.py`)

Every worker is a small `Adapter` subclass plus `runtime.run(adapter)`. The runner owns the parts that must behave the same for all agents: claim, the `<results-directory>/<task-id>/` artifact folder, `process.json`, `progress.jsonl`, `events.jsonl`, the timeout, report validation, and the `delivered` / `blocked` transition. The adapter owns only the agent-specific parts:

| Method | Purpose |
| --- | --- |
| `name` / `display_name` | queue worker id and UI label |
| `configure(parser)` | agent-specific command line options |
| `validate(parser, args)` | reject invalid options |
| `metadata(args)` | extra fields for `process.json` (model label, permission mode, ...) |
| `progress_details(args)` | extra fields on the claimed progress event |
| `build_prompt(args, task_id, brief)` | the prompt; it must contain the exact `TASK_ID` |
| `build_command(args, prompt)` | `(argv, env)` for the agent process |
| `parse_event(event)` | map stdout events to `init` / `text` / `tool` / `failure` / `result` |
| `extract_report(state)` | return the report text from the final event |

The default parser reads the standard event protocol, one JSON object per line on stdout:

```text
{"type": "init", "model": "..."}                    optional startup notice
{"type": "text", "text": "..."}                     public progress text
{"type": "tool_call", "tool": "read"}               a tool invocation
{"type": "tool_result", "status": "failed"}         status failed/error/denied counts as a tool error
{"type": "error", "message": "..."}                 agent-side error
{"type": "final", "text": "<report>"}               the final report
```

An agent that already prints this format needs no custom parser. An agent with its own format overrides `parse_event` and `extract_report` instead.

The final report must contain the exact task ID and exactly one separate line `TASK_STATUS=COMPLETED` or `TASK_STATUS=BLOCKED`, and the agent process must exit 0. A tool error, a missing or ambiguous status, a missing task ID, a timeout or a non-zero exit marks the task `blocked`. The worker writes `report.md` itself from the final reply; prompts should tell the endpoint to put the report in its final reply and not create report files, because a read-only endpoint that writes files gets a sandbox denial that counts as a tool error.

## Zero-code integration (`workers/universal.py`)

An agent that accepts a prompt argument and prints its report to stdout joins without writing code:

```powershell
python src/workers/universal.py --db <database-path> --output-dir <results-directory> --agent <agent-id> --command "<cli> {prompt}" --format auto
```

The template is split with POSIX shell quoting, so use forward slashes in paths; `{prompt}`, `{task_id}` and `{brief}` are substituted, and with no placeholder the prompt is appended as the last argument. `--format text` treats the whole stdout as the report, `--format jsonl` expects the standard event protocol, and the default `auto` accepts both. The runner still records the artifacts, enforces the timeout and validates the report.

## Adding a new agent

1. Copy `src/workers/echo.py` to `src/workers/<agent>.py`; it is the minimal reference adapter and runs without any external CLI.
2. Set `name` and `display_name`, implement `build_prompt` and `build_command`.
3. If the CLI does not print the standard event protocol, override `parse_event` (see `src/workers/dsh.py` for a real example with a different field layout).
4. Publish with `publish <agent-id> <brief-path>` and run `python src/workers/<agent>.py --db ... --output-dir ...`.
5. The progress window and side panel pick the new agent up automatically; they label known workers and show any other id in uppercase.

If the agent only needs to run and report, prefer the zero-code runner above or the manual three-command contract in [docs/AGENT-CONTRACT.md](docs/AGENT-CONTRACT.md); write an adapter only when its output format needs mapping.

## Built-in adapters

### WB (`src/workers/wb.py`)

```powershell
python src/queue.py --db <database-path> publish wb <brief-path>
python -u src/workers/wb.py --db <database-path> --output-dir <results-directory>
```

The worker atomically claims one pending WB task, launches CodeBuddy, streams compact progress to stdout, saves `progress.jsonl`, process metadata, the full event stream and the report under a task-ID directory, and updates the queue. An empty queue exits without launching WB. The default model is `glm-5.3`; the panel displays the model recorded by the CLI init event and `--model` permits an explicit override. By default it requests Read only. For a task whose file edits are authorized and scoped, add `--allow-edits`; the CLI then uses Read, Write and Edit with `acceptEdits`. For authorized command work, add `--allow-bash-command <exact command>` for each approved command and set `--cwd` explicitly. The worker configures process-scoped exact Bash permission rules; unsupported wildcards, shell operators and other metacharacters are rejected. Do not grant broad Bash access or send WB desktop-control tasks. Neither the brief's file scope nor command permission rules are a proven sandbox: commands may themselves modify files or execute project code, and existing CLI permission sources can affect decisions. Review outputs and actual changes. CLI location, model, working directory and timeout are configurable with `--help`.

### DSH (`src/workers/dsh.py`)

```powershell
python src/queue.py --db <database-path> publish dsh <brief-path>
python -u src/workers/dsh.py --db <database-path> --output-dir <results-directory> --patch <overlay-path> --model-label <desktop-model-label>
```

DSH is a headless CLI session. Launch it through `DeepSeek Harness.exe --expose-internals` pointing at `dsh-desktop-host/lib/cli.js`. `workers/dsh.py` takes `--cli` from `$DSH_CLI` (or an explicit `--cli`) and derives `--entrypoint` from it, so a standard Windows install only needs the environment variable or the option. The shipped `headless` profile disables credential storage, so `workers/dsh.py` requires `--patch` pointing to an overlay that enables the `credentials`, `authorization` and `deepseek-account` components and adds the model route. The overlay is environment-specific: prepare it yourself and never commit secret values; without it the worker fails with a missing-credential error, and you must not ask the user to paste keys into the conversation.

Read-only is the default (`--permission-mode read-only`); use `--permission-mode workspace-write` only for a task whose file edits the user explicitly authorized, and still review what actually changed. `--model-label` records the model the route was configured to use; it is not proof of the serving model. The events are written to `events.raw.jsonl` and `events.jsonl`; the endpoint is a headless CLI session, not the Codex or DSH desktop conversation the user sees.

### Echo (`workers/echo.py`)

A template and self-test adapter. It speaks the standard event protocol in-process, so it works without any external CLI:

```powershell
python src/queue.py --db <database-path> publish echo <brief-path>
python src/workers/echo.py --db <database-path> --output-dir <results-directory>
```

Add `--fail` to simulate a tool error and verify that the task is blocked even when the agent claims completion.

## Progress window and side panel

Queue, progress window and side panel are agent-neutral and show every worker id.

For a visible Windows progress window, run `python src/progress.py --db <database-path> --output-dir <results-directory>` with the same paths as the worker. It refreshes every second, prefixes each task with its agent id, and shows the latest recorded stage and reports awaiting review. Keep the worker running separately; the window only reads status and does not launch or control tasks. Opening or closing the window does not change the queue. To read status without a window, add `--snapshot`.

For a detailed side panel, launch `python src/dashboard_supervisor.py --db <absolute-database-path> --output-dir <absolute-results-directory> --url-file <absolute-private-url-file> --status-file <absolute-status-file>` as a local background process, then open the generated URL in the chat's browser panel. The supervisor restarts its own server child at the same address after an unexpected exit, up to three times in 60 seconds; inspect the status file and adjacent log if recovery stops. It is not an OS service and does not survive its own termination or a computer restart. The server uses loopback with a random URL token, displays the running task by default, and uses a hover-revealed right sidebar to select other sessions. Public messages, compact expandable tool calls and their results update once a second. Keep the URL and task data private. Large tool outputs are truncated in the UI; full events are in `events.jsonl`. Public progress messages are requested from the agent but depend on its actual output; do not treat internal thinking blocks as public decision records. The panel is a local browser view, not a native WorkBuddy or DSH desktop integration. Every worker built on `runtime` (`dsh`, `echo`, `workers/universal.py`) accepts `--open-sidebar`: when it claims a task it starts this dashboard if needed and opens the URL in the default browser. To do that on demand, run `python src/sidebar.py` (add `--no-open` to only print the URL and reuse an already running dashboard).

If the supervisor itself stops, relaunch it with the same paths; it reuses the saved local URL and fails safely if the port is in use. Do not start a second supervisor using another status file for the same URL.

## Recovery and review

The worker processes one task per invocation; it is not a background polling service. Abrupt termination of the worker or machine can leave a task `running`. Run `python src/recover.py --db <database-path> --output-dir <results-directory> --worker <agent-id> <task-id>` to inspect metadata and recorded PIDs. Inspect processes and artifacts independently: PIDs can be reused, metadata may be missing, and a crash can occur between launching a process and recording its PID. Only after confirming all relevant processes stopped, repeat with `--confirmed-stopped` to mark it blocked. The command refuses if a recorded PID is active, and never retries or kills a process. Do not blindly rerun a possibly active or partially executed task.

`delivered` means a report was returned and the worker reported completion; it is not independent acceptance. Inspect the report, events and actual changes before claiming success. Reports and event logs may contain task data; keep them appropriately private.

The verified WB integration uses the standalone CodeBuddy CLI; it does not prove WorkBuddy desktop displays the session. The verified DSH integration uses the headless CLI; it does not prove the desktop application behaves the same.