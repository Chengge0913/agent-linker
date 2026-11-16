"""Run one queued task on behalf of any CLI agent.

Integration is a small Adapter subclass: it declares the queue worker id and
builds the agent's command line. This module owns the lifecycle that is the
same for every agent: claim one task, create the artifact directory, record
process metadata and progress events, enforce the timeout, validate the final
report, and finish the task as delivered or blocked.

An agent that prints one JSON object per line on stdout needs no parser; the
default parser understands this standard protocol:

    {"type": "init", "model": "..."}                  optional startup notice
    {"type": "text", "text": "..."}                   public progress text
    {"type": "tool_call", "tool": "read"}             a tool invocation
    {"type": "tool_result", "status": "failed"}       status failed/error/denied
    {"type": "error", "message": "..."}               agent-side error
    {"type": "final", "text": "<report>"}             the final report

The final report must contain the exact TASK_ID and exactly one separate line
TASK_STATUS=COMPLETED or TASK_STATUS=BLOCKED. Anything else blocks the task,
and so does a failed tool event.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRESS_PATH = None
TASK_STATUS = re.compile(r'^TASK_STATUS=(COMPLETED|BLOCKED)\s*$', re.MULTILINE)


def queue(db, *args):
    result = subprocess.run([sys.executable, str(HERE / 'queue.py'), '--db', str(db), *args],
                            capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


def progress(task_id, stage, worker=None, **details):
    event = {'time': datetime.now(timezone.utc).isoformat(), 'task_id': task_id, 'stage': stage}
    if worker:
        event['worker'] = worker
    event.update(details)
    line = json.dumps(event, ensure_ascii=False)
    if PROGRESS_PATH is not None:
        with PROGRESS_PATH.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
    print(line, flush=True)


class Adapter:
    """Agent-specific part of a worker. Subclass it and call run().

    name:         queue worker id (lowercase, e.g. 'dsh', 'wb', 'claude-code')
    display_name: human label used in progress output and the dashboard
    raw_events:   copy every stdout line verbatim into events.raw.jsonl
    """

    name = ''
    display_name = ''
    raw_events = False

    def configure(self, parser):
        """Add agent-specific command line options."""

    def validate(self, parser, args):
        """Raise parser.error() for invalid arguments."""

    def prepare(self, args):
        """Remember parsed arguments before the task starts."""
        self.args = args

    def metadata(self, args):
        """Extra fields written to process.json."""
        return {}

    def progress_details(self, args):
        """Extra fields recorded on the claimed progress event."""
        return {}

    def build_prompt(self, args, task_id, brief):
        """Return the prompt sent to the agent. Must include TASK_ID."""
        raise NotImplementedError

    def build_command(self, args, prompt):
        """Return (argv, env) for the agent process; env=None inherits."""
        raise NotImplementedError

    def parse_event(self, event):
        """Map one stdout event to {'kind': init|text|tool|failure|result}."""
        kind = event.get('type')
        if kind == 'init':
            return {'kind': 'init', 'model': event.get('model'),
                    'model_source': event.get('model_source')}
        if kind == 'text':
            return {'kind': 'text'}
        if kind == 'tool_call':
            return {'kind': 'tool', 'tool': event.get('tool')}
        if kind == 'tool_result':
            if str(event.get('status', '')).lower() in ('failed', 'error', 'denied'):
                return {'kind': 'failure'}
            return None
        if kind in ('error', 'fatal'):
            return {'kind': 'failure'}
        if kind == 'final':
            return {'kind': 'result'}
        return None

    def extract_report(self, state):
        """Return the report text from the final event."""
        final = state.get('final')
        return final.get('text') if isinstance(final, dict) else None


def run(adapter, argv=None):
    """Run one queued task through the adapter. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description=adapter.__doc__ or f'Run one queued task for the {adapter.display_name or adapter.name} agent.')
    parser.add_argument('--db', required=True, type=Path, help='SQLite task ledger')
    parser.add_argument('--output-dir', required=True, type=Path, help='one artifact folder per task id')
    parser.add_argument('--timeout', type=int, default=300, help='seconds before the agent is stopped')
    parser.add_argument('--cwd', type=Path, default=Path.cwd(), help='working directory for the agent')
    parser.add_argument('--open-sidebar', action='store_true',
                        help='open the activity dashboard in the browser when a task starts')
    adapter.configure(parser)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    adapter.validate(parser, args)
    adapter.prepare(args)
    args.db = args.db.resolve()
    output = args.output_dir.resolve()
    task = queue(args.db, 'claim', adapter.name)
    if not task:
        progress(None, 'idle', worker=adapter.name)
        return 0
    task_id = task['id']
    if getattr(args, 'open_sidebar', False):
        try:
            from sidebar import ensure_sidebar
            ensure_sidebar(db=args.db, output_dir=output, open_browser=True)
        except Exception as exc:
            print(f'could not open the activity sidebar: {exc}', file=sys.stderr)
    folder = output / task_id
    process = timer = None
    timed_out = threading.Event()
    try:
        folder.mkdir(parents=True, exist_ok=False)
        global PROGRESS_PATH
        PROGRESS_PATH = folder / 'progress.jsonl'
        metadata = {'task_id': task_id, 'worker_pid': os.getpid(), 'child_pid': None,
                    'agent': adapter.name, 'display_name': adapter.display_name,
                    'started_at': datetime.now(timezone.utc).isoformat(),
                    **adapter.metadata(args)}
        (folder / 'process.json').write_text(json.dumps(metadata), encoding='utf-8')
        progress(task_id, 'claimed', worker=adapter.name, brief=task['brief'],
                 **adapter.progress_details(args))
        brief = Path(task['brief'])
        if not brief.is_file():
            raise RuntimeError('task brief is missing')
        prompt = adapter.build_prompt(args, task_id, brief)
        command, env = adapter.build_command(args, prompt)
        final = None
        failed_tool = False
        with ExitStack() as stack:
            raw = (stack.enter_context((folder / 'events.raw.jsonl').open('w', encoding='utf-8'))
                   if adapter.raw_events else None)
            events = stack.enter_context((folder / 'events.jsonl').open('w', encoding='utf-8'))
            errors = stack.enter_context((folder / 'stderr.log').open('w', encoding='utf-8'))
            process = subprocess.Popen(command, cwd=args.cwd, stdout=subprocess.PIPE, stderr=errors,
                                       text=True, encoding='utf-8', errors='replace', bufsize=1, env=env)
            metadata['child_pid'] = process.pid
            temp = folder / 'process.json.tmp'
            temp.write_text(json.dumps(metadata), encoding='utf-8')
            temp.replace(folder / 'process.json')

            def expire():
                timed_out.set()
                try:
                    process.kill()
                except OSError:
                    pass

            timer = threading.Timer(args.timeout, expire)
            timer.start()
            for line in process.stdout:
                if raw is not None:
                    raw.write(line)
                    raw.flush()
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError
                except ValueError:
                    event = {'type': 'raw', 'text': line.rstrip('\n')}
                event.setdefault('__timestamp', datetime.now(timezone.utc).isoformat())
                events.write(json.dumps(event, ensure_ascii=False) + '\n')
                events.flush()
                parsed = adapter.parse_event(event) or {}
                kind = parsed.get('kind')
                if kind == 'init':
                    progress(task_id, 'init', worker=adapter.name,
                             model=parsed.get('model'), model_source=parsed.get('model_source'))
                elif kind == 'tool':
                    progress(task_id, 'tool', worker=adapter.name, tool=parsed.get('tool'))
                elif kind == 'failure':
                    failed_tool = True
                elif kind == 'result':
                    final = event
                    progress(task_id, 'result_received', worker=adapter.name)
            exit_code = process.wait()
            timer.cancel()
        if timed_out.is_set():
            raise RuntimeError(f'CLI timed out after {args.timeout} seconds')
        if exit_code != 0:
            raise RuntimeError(f'CLI exited with code {exit_code}')
        report = adapter.extract_report({'final': final})
        if not isinstance(report, str) or task_id not in report:
            raise RuntimeError('final report missing matching task ID')
        report_path = folder / 'report.md'
        report_path.write_text(report, encoding='utf-8')
        statuses = TASK_STATUS.findall(report)
        if statuses != ['COMPLETED'] or failed_tool:
            queue(args.db, 'finish', adapter.name, task_id, str(report_path), '--blocked')
            progress(task_id, 'blocked', worker=adapter.name,
                     reason='Tool error or incomplete/ambiguous report', report=str(report_path))
            return 1
        queue(args.db, 'finish', adapter.name, task_id, str(report_path))
        progress(task_id, 'delivered', worker=adapter.name, report=str(report_path))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        if timer:
            timer.cancel()
        if process and process.poll() is None:
            process.kill()
            process.wait()
        reason = f'{type(exc).__name__}: {exc}; artifacts: {folder}'
        try:
            queue(args.db, 'finish', adapter.name, task_id, reason, '--blocked')
            progress(task_id, 'blocked', worker=adapter.name, reason=reason)
        except Exception as finish_error:
            print(f'Could not mark {task_id} blocked: {finish_error}', file=sys.stderr)
        return 1