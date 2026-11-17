"""Claim one DSH task and run it through the headless profile."""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import runtime

def discover_cli(environ, candidates=None):
    if environ.get('DSH_CLI'):
        return Path(environ['DSH_CLI'])
    if candidates is None:
        candidates = [Path('D:/Deepseek Harness/DeepSeek Harness.exe')]
    return next ( (path for path in candidates if path.is_file()), None)


ENTRY_SUFFIX = Path('resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js')


def overlay_model_route(path):
    """Recognize a simple agent-default-model entry; never guess complex YAML."""
    try:
        text = path.read_text(encoding='utf-8-sig')
    except (OSError, UnicodeError):
        return None
    blocks = re.split(r'(?m)^- id:\s*', text)
    for block in blocks[1:]:
        lines = block.splitlines()
        if not lines or lines[0].strip() != 'agent-default-model':
            continue
        matches = re.fullmatch(
            r'\s*config:\s*\n[ \t]+provider:[ \t]*([A-Za-z0-9_.-]+)[ \t]*\n[ \t]+model:[ \t]*([A-Za-z0-9_.-]+)[ \t]*',
            '\n'.join(lines[1:]))
        if matches:
            return matches.group(1), matches.group(2)
    return None


def valid_entrypoint(path):
    if path.is_file():
        return True
    return any(parent.name.lower().endswith('.asar') and parent.is_file()
               for parent in path.parents)


class DshAdapter(runtime.Adapter):
    name = 'dsh'
    display_name = 'DSH'
    raw_events = True

    def configure(self, parser):
        parser.add_argument('--patch', required=True, type=Path, help='DSH overlay patch that supplies credentials')
        parser.add_argument('--cli', type=Path, default=discover_cli(os.environ),
                            help='DeepSeek Harness executable (default: DSH_CLI, then known install path)')
        parser.add_argument('--entrypoint', type=Path, default=None,
                            help='cli.js entrypoint (default: derived from --cli)')
        parser.add_argument('--model-label', default=None, help='Configured model label; not proof of the serving model')
        parser.add_argument('--permission-mode', default='read-only',
                            choices=('read-only', 'workspace-write'),
                            help='DSH permission preset; workspace-write only for explicitly authorized file edits')

    def validate(self, parser, args):
        if args.cli is None or not args.cli.is_file():
            parser.error('DSH CLI not found; pass --cli (or set DSH_CLI) pointing to the DeepSeek Harness executable')
        if args.entrypoint is None:
            args.entrypoint = args.cli.parent / ENTRY_SUFFIX
        if not valid_entrypoint(args.entrypoint):
            parser.error(f'DSH entrypoint not found at {args.entrypoint}; pass --entrypoint pointing to cli.js')
        if not args.patch.is_file():
            parser.error('patch file must exist; pass --patch with an overlay that supplies DSH credentials')
        if not args.model_label:
            route = overlay_model_route(args.patch)
            if route:
                args.model_label = f'{route[1]} / {route[0]}'
        if not args.cwd.is_dir():
            parser.error('working directory must exist; pass --cwd')

    def metadata(self, args):
        return {'configured_model': args.model_label, 'permission_mode': args.permission_mode}

    def progress_details(self, args):
        return {'permission_mode': args.permission_mode}

    def build_prompt(self, args, task_id, brief):
        scope = ('Perform only its authorized read-only work; do not run commands, edit files, or delegate, '
                 'and do not create any file (the worker saves your final reply as the report). '
                 if args.permission_mode == 'read-only' else
                 'Perform only the file changes explicitly authorized by the brief; keep the writable scope minimal, '
                 'do not create a report file, and do not delegate or run unrelated commands. ')
        return (f'TASK_ID={task_id} Read the task brief at {brief}. ' + scope +
                'Give brief public progress updates in Chinese before meaningful actions. '
                'Put the report in your final reply only; the worker writes it out for you. '
                f'In your final report include the exact TASK_ID={task_id} and exactly one separate line '
                'TASK_STATUS=COMPLETED or TASK_STATUS=BLOCKED. Mark BLOCKED if required work is incomplete.')

    def build_command(self, args, prompt):
        command = [str(args.cli)]
        if args.cli.name.lower().startswith('deepseek'):
            command.append('--expose-internals')
        command += [str(args.entrypoint), '--profile', 'headless', '--patch', str(args.patch.resolve()),
                    '--json', '--permission-mode', args.permission_mode, '--approval-policy', 'never', prompt]
        env = os.environ.copy()
        env['ELECTRON_RUN_AS_NODE'] = '1'
        return command, env

    def parse_event(self, event):
        kind = event.get('type')
        if kind == 'session':
            return {'kind': 'init', 'model': self.args.model_label, 'model_source': '配置'}
        if kind == 'tool_call':
            return {'kind': 'tool', 'tool': event.get('tool')}
        if kind == 'tool_result':
            if str(event.get('status', '')).lower() in ('failed', 'error', 'denied'):
                return {'kind': 'failure'}
            return None
        if kind == 'error':
            return {'kind': 'failure'}
        if kind == 'final':
            return {'kind': 'result'}
        return None


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    return runtime.run(DshAdapter())


if __name__ == '__main__':
    sys.exit(main())
