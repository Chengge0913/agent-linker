# Agent worker contract (v1)

This is the portable contract behind `agent-linker`. Any agent that can
run a command line can join the shared task queue and return evidence, without
changing the queue, the progress window or the side panel. Read this file first;
it is self-contained and does not assume a specific agent product or model.

Two integration modes are supported, and they can be mixed:

| Mode | Who writes code | What you get |
| --- | --- | --- |
| A. Manual protocol | Nobody | claim / work / finish with three queue commands |
| B. Runner | One small adapter, or a command template | artifacts, events, timeout, report validation, state transition |

## 1. Roles

- **Lead**: the person or agent that owns the goal. It defines a brief, may
  split work into independent tasks, publishes them, and reviews every receipt.
- **Worker**: any agent that claims one task at a time and returns a report.
  A worker id is a lowercase name such as `wb`, `dsh`, `claude-code` or
  `my-agent` (letters, digits and hyphens, at most 32 characters).

The ledger is a local SQLite file shared by all participants. It stores the
brief path, the state and a receipt; it never stores prompts, model output or
credentials.

## 2. Queue protocol

Run from the directory that contains `queue.py`, always with the same database
path:

```text
python src/queue.py --db <db> publish <agent-id> <brief-path>   -> {"id": "...", "state": "pending"}
python src/queue.py --db <db> claim <agent-id>                   -> task JSON, or null when idle
python src/queue.py --db <db> finish <agent-id> <task-id> <receipt> [--blocked]
python src/queue.py --db <db> status                             -> all tasks
```

States: `pending` → `running` → `delivered` | `blocked`.

Rules:

- A worker claims only tasks addressed to its own id, one at a time.
- A worker can finish only a task it currently holds in `running`.
- `receipt` is a report path or a blocker description; it must not be empty.
- `finish` without `--blocked` means the worker reports completion. It is not
  independent acceptance; the lead still reviews the report and the changes.

## 3. Minimum contract (mode A: manual)

This mode needs no Python: any agent that can read a file, run commands and
print a report can join.

1. `claim` your task id; the JSON contains the `brief` path and the `id`.
2. Read the brief. Work only inside its allowed scope and with its allowed
   actions. Do not create files the brief did not ask for.
3. Write your report, ending with the exact task id and exactly one status line:

   ```text
   TASK_ID=<the 32-character task id from claim>
   TASK_STATUS=COMPLETED
   ```

   Use `TASK_STATUS=BLOCKED` when a required part is unfinished; then list the
   blocker instead of claiming success.
4. Call `finish` with the report path (or the blocker text plus `--blocked`).
5. Exit with code 0. Report the task id and status; never claim work you did
   not do.

A report may also be saved anywhere the lead can read. The queue stores only
its path; nothing is copied into the database.

## 4. Standard event protocol (mode B)

If you run under a runner (or want the progress window to show your actions),
print one JSON object per line on stdout:

```text
{"type": "init", "model": "..."}                  optional startup notice
{"type": "text", "text": "..."}                   public progress text
{"type": "tool_call", "tool": "read"}             a tool invocation
{"type": "tool_result", "status": "failed"}       failed/error/denied = tool error
{"type": "error", "message": "..."}               agent-side error
{"type": "final", "text": "<report>"}             the final report
```

Rules:

- Every other line is treated as plain report text by the auto format; a
  `tool_result` whose status is `failed`, `error` or `denied`, or an `error`
  event, marks the task blocked even if the final report claims completion.
- The final report must satisfy section 3, step 3.
- The process must exit 0.

## 5. Adapter interface (mode B: `runtime.py`)

`runtime.run(adapter)` owns the shared lifecycle: claim, artifact folder
`<output-dir>/<task-id>/`, `process.json`, `progress.jsonl`, `events.jsonl`,
`stderr.log`, the timeout, report validation and the final state transition.
Subclass `Adapter` and implement only the agent-specific parts:

| Member | Purpose |
| --- | --- |
| `name`, `display_name` | queue worker id and UI label |
| `configure(parser)`, `validate(parser, args)` | agent-specific options |
| `build_prompt(args, task_id, brief)` | prompt; must contain the exact `TASK_ID` |
| `build_command(args, prompt)` | `(argv, env)`; `env=None` inherits |
| `parse_event(event)` | map stdout events to `init`/`text`/`tool`/`failure`/`result` |
| `metadata(args)`, `progress_details(args)` | extra fields for `process.json` / progress |
| `extract_report(state)` | final report text (default: `final.text`) |

`src/workers/echo.py` is the minimal reference (no external CLI). `src/workers/dsh.py` is a
real adapter for a different event layout.

## 6. Zero-code runner (`src/workers/universal.py`)

For an agent that already accepts a prompt and prints a report there is no
adapter to write:

```text
python src/workers/universal.py --db <db> --output-dir <out> --agent <agent-id> \
    --command "<cli> --print {prompt}" --format auto|text|jsonl
```

- `{prompt}`, `{task_id}` and `{brief}` are substituted in the template; with no
  placeholder the prompt is appended as the last argument.
- The template is split with POSIX shell quoting; use forward slashes in paths.
- `text` treats the whole stdout as the report, `jsonl` expects the standard
  event protocol, `auto` (default) parses JSON lines as events and keeps the
  rest as text.

## 7. Verification checklist for the lead

Reject a receipt unless all of these hold:

- [ ] the receipt path exists and its report contains the task id;
- [ ] exactly one `TASK_STATUS=` line and it says `COMPLETED` or `BLOCKED`;
- [ ] the worker process exited 0 for `delivered`;
- [ ] `events.jsonl` shows no failed tool result or agent error;
- [ ] the actual changes stay inside the brief's allowed scope;
- [ ] for `blocked`, the blocker is concrete and the unfinished work is not
      described as done.

## 8. Safety boundaries

- Read-only tasks must not create or modify files. A sandbox denial is a tool
  error and blocks the task; prompts should say the report goes in the final
  reply.
- Write tasks are allowed only when the user authorized the specific scope; a
  brief's file list and a CLI's permission flags are not a proven sandbox.
- Keep the database, briefs, reports and progress artifacts private; they may
  contain task data. Never put secrets in a brief.
- The worker is not a background service: it runs one claimed task per
  invocation. A crashed worker can leave a task `running`; use
  `recover.py --worker <agent-id> <task-id>` and confirm the process stopped
  before marking it blocked.

## 9. Compatibility

| Worker | Entry point | Notes |
| --- | --- | --- |
| WB CodeBuddy | `src/workers/wb.py` | standalone CodeBuddy CLI, default `glm-5.3` |
| DSH | `src/workers/dsh.py` | headless CLI, needs a credential overlay `--patch` |
| Echo | `src/workers/echo.py` | template/self-test, no external CLI |
| Any CLI agent | `src/workers/universal.py` | command template only, no code |
| Custom | `runtime.py` | small `Adapter` subclass |