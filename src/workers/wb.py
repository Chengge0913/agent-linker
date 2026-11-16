"""Claim and run one WB task through the local CodeBuddy CLI."""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRESS_PATH = None


def queue(db, *args):
    result = subprocess.run([sys.executable, str(HERE.parent / 'queue.py'), '--db', str(db), *args],
                            capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


def progress(task_id, stage, **details):
    event = {'time': datetime.now(timezone.utc).isoformat(), 'task_id': task_id, 'stage': stage, **details}
    line = json.dumps(event, ensure_ascii=False)
    if PROGRESS_PATH is not None:
        with PROGRESS_PATH.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
    print(line, flush=True)


def main():
    global PROGRESS_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--cli', default=os.environ.get('CODEBUDDY_CLI') or str(Path.home() / 'AppData/Local/codebuddy/bin/codebuddy.exe'))
    parser.add_argument('--cli-prefix', action='append', default=[], help='Arguments before CodeBuddy options')
    parser.add_argument('--model', default='glm-5.3')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--cwd', type=Path, default=Path.cwd())
    parser.add_argument('--allow-edits', action='store_true', help='Enable file editing for an explicitly authorized task')
    parser.add_argument('--allow-bash-command', action='append', default=[], metavar='COMMAND',
                        help='Allow one exact, explicitly authorized Bash command; repeat for additional commands')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    for allowed_command in args.allow_bash_command:
        if not allowed_command.strip() or re.search(r'[\x00-\x1f*?\[\](),;|&<>`$]', allowed_command):
            parser.error('command rules must be nonempty exact commands without wildcards or shell operators')
    args.db = args.db.resolve()
    args.output_dir = args.output_dir.resolve()
    task = queue(args.db, 'claim', 'wb')
    if not task:
        progress(None, 'idle')
        return 0
    task_id = task['id']
    folder = args.output_dir / task_id
    timer = None
    process = None
    timed_out = threading.Event()
    try:
        folder.mkdir(parents=True, exist_ok=False)
        PROGRESS_PATH = folder / 'progress.jsonl'
        metadata = {'task_id': task_id, 'worker_pid': os.getpid(), 'child_pid': None,
                    'started_at': datetime.now(timezone.utc).isoformat()}
        (folder / 'process.json').write_text(json.dumps(metadata), encoding='utf-8')
        progress(task_id, 'claimed', brief=task['brief'])
        brief = Path(task['brief'])
        if not brief.is_file():
            raise RuntimeError('task brief is missing')
        scope = ('Perform only the authorized work described in the brief, respecting its file scope. '
                 if args.allow_edits or args.allow_bash_command else 'Perform only the authorized read-only work described in the brief. ')
        prompt = (f'TASK_ID={task_id} Read the task brief at {brief}. '
                  + scope +
                  'Give brief public progress updates in Chinese before meaningful actions, including your next step and concise reason; do not reveal private reasoning. '
                  + ('Run only these exact commands: ' + json.dumps(args.allow_bash_command, ensure_ascii=False) + '. Do not append commands, redirections, or shell operators; the tool provides the exit code. ' if args.allow_bash_command else '') +
                  f'Include the exact TASK_ID={task_id} in your final report. '
                  'Include exactly one separate line TASK_STATUS=COMPLETED or TASK_STATUS=BLOCKED; '
                  'choose BLOCKED if any required work could not be completed. '
                  'Report blockers and do not claim completion for work not done.')
        tools = ['Read'] + (['Write', 'Edit'] if args.allow_edits else []) + (['Bash'] if args.allow_bash_command else [])
        allowed = ['Read'] + (['Write', 'Edit'] if args.allow_edits else []) + [f'Bash({entry})' for entry in args.allow_bash_command]
        command = [args.cli, *args.cli_prefix, '-p', '--model', args.model,
                   '--output-format', 'stream-json', '--verbose', '--tools', ','.join(tools),
                   '--allowedTools', ','.join(allowed), '--permission-mode',
                   'acceptEdits' if args.allow_edits else 'dontAsk']
        if args.allow_bash_command:
            command += ['--settings', json.dumps({'permissions': {'allow': [f'Bash({entry})' for entry in args.allow_bash_command]}})]
        command.append(prompt)
        final = None
        permission_denied = False
        with (folder / 'events.jsonl').open('w', encoding='utf-8') as events, \
             (folder / 'stderr.log').open('w', encoding='utf-8') as errors:
            process = subprocess.Popen(command, cwd=args.cwd, stdout=subprocess.PIPE, stderr=errors,
                                       text=True, encoding='utf-8', errors='replace', bufsize=1)
            metadata['child_pid'] = process.pid
            temp_metadata = folder / 'process.json.tmp'
            temp_metadata.write_text(json.dumps(metadata), encoding='utf-8')
            temp_metadata.replace(folder / 'process.json')
            def expire():
                timed_out.set()
                process.kill()
            timer = threading.Timer(args.timeout, expire)
            timer.start()
            for line in process.stdout:
                events.write(line)
                events.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get('type') == 'result':
                    final = event
                    progress(task_id, 'result_received', subtype=event.get('subtype'))
                elif event.get('type') == 'system' and event.get('subtype') == 'init':
                    progress(task_id, 'init', model=event.get('model'))
                elif event.get('type') == 'assistant':
                    for part in event.get('message', {}).get('content', []):
                        if part.get('type') == 'tool_use':
                            progress(task_id, 'tool', tool=part.get('name'))
                elif event.get('type') == 'user':
                    for part in event.get('message', {}).get('content', []):
                        if part.get('type') == 'tool_result' and part.get('is_error'):
                            error_text = json.dumps(part.get('content', ''), ensure_ascii=False).lower()
                            if ('permission' in error_text and 'denied' in error_text) or ('权限' in error_text and '拒绝' in error_text):
                                permission_denied = True
            exit_code = process.wait()
            timer.cancel()
        if timed_out.is_set():
            raise RuntimeError(f'CLI timed out after {args.timeout} seconds')
        if exit_code != 0:
            raise RuntimeError(f'CLI exited with code {exit_code}')
        if not final or final.get('is_error') or final.get('subtype') != 'success':
            raise RuntimeError('CLI did not return a successful final event')
        report = final.get('result')
        if not isinstance(report, str) or task_id not in report:
            raise RuntimeError('final report missing matching task ID')
        report_path = folder / 'report.md'
        report_path.write_text(report, encoding='utf-8')
        statuses = re.findall(r'^TASK_STATUS=(COMPLETED|BLOCKED)\s*$', report, re.MULTILINE)
        if statuses != ['COMPLETED'] or permission_denied:
            queue(args.db, 'finish', 'wb', task_id, str(report_path), '--blocked')
            progress(task_id, 'blocked', reason='Permission denied, worker reported a blocker, or status was ambiguous', report=str(report_path))
            return 1
        queue(args.db, 'finish', 'wb', task_id, str(report_path))
        progress(task_id, 'delivered', report=str(report_path))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        if timer:
            timer.cancel()
        if process and process.poll() is None:
            process.kill()
            process.wait()
        reason = f'{type(exc).__name__}: {exc}; artifacts: {folder}'
        try:
            queue(args.db, 'finish', 'wb', task_id, reason, '--blocked')
            progress(task_id, 'blocked', reason=reason)
        except Exception as finish_error:
            print(f'Could not mark {task_id} blocked: {finish_error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
