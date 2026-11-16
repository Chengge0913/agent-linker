"""Minimal agent adapter: demonstrates the standard stdout event protocol.

This worker is a template for connecting a new agent. It speaks the standard
JSONL event protocol, so it needs no custom parser, and it runs in-process
with only the Python standard library.

Run it like any other worker:

    python src/queue.py --db queue.sqlite publish echo brief.md
    python src/workers/echo.py --db queue.sqlite --output-dir results

Add --fail to simulate a tool error and verify the blocked path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import runtime

ECHO_SCRIPT = r'''
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
prompt, mode = sys.argv[1], sys.argv[2]
task_id = prompt.split('TASK_ID=')[1].split()[0]
print(json.dumps({'type': 'init', 'model': 'echo-demo'}), flush=True)
print(json.dumps({'type': 'text', 'text': '收到工单，开始核对。'}), flush=True)
print(json.dumps({'type': 'tool_call', 'tool': 'read', 'input': {'file': 'brief.md'}}), flush=True)
if mode == 'fail':
    print(json.dumps({'type': 'tool_result', 'status': 'failed', 'result': 'permission denied'}), flush=True)
    print(json.dumps({'type': 'final', 'text': 'TASK_ID=%s\nTASK_STATUS=COMPLETED\n示例执行端自称完成，但工具已失败。' % task_id}), flush=True)
else:
    print(json.dumps({'type': 'tool_result', 'status': 'completed', 'result': 'ok'}), flush=True)
    print(json.dumps({'type': 'final', 'text': 'TASK_ID=%s\nTASK_STATUS=COMPLETED\n示例执行端已完成核对。' % task_id}), flush=True)
'''


class EchoAdapter(runtime.Adapter):
    name = 'echo'
    display_name = 'Echo'

    def configure(self, parser):
        parser.add_argument('--fail', action='store_true', help='report a tool error to exercise the blocked path')

    def build_prompt(self, args, task_id, brief):
        return f'TASK_ID={task_id} Read the brief at {brief} and report the result.'

    def build_command(self, args, prompt):
        return [sys.executable, '-c', ECHO_SCRIPT, prompt, 'fail' if args.fail else 'ok'], None


def main():
    return runtime.run(EchoAdapter())


if __name__ == '__main__':
    sys.exit(main())
