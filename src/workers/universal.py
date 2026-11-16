"""Run one queued task through any CLI agent, without writing an adapter.

Any agent that can be launched from a command line can join the queue with this
script: pass its command template and let the agent write its report to stdout.

    python src/workers/universal.py --db queue.sqlite --output-dir out \\
        --agent claude-code --command "claude -p {prompt}"

Placeholders in --command: {prompt}, {task_id}, {brief}. When none is present,
the prompt is appended as the last argument. The template is split with POSIX
shell quoting, so use forward slashes in paths (or quote each argument).

Output formats:
  text   the whole stdout is the final report
  jsonl  stdout is the standard JSONL event protocol
  auto   (default) JSON lines are parsed as events, other lines are report text

The lead still reviews the report and the actual changes: `delivered` is not
independent acceptance.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import os
import re
import shlex
import sys

import runtime

AGENT_ID = re.compile(r'^[a-z][a-z0-9-]{0,31}$')

PROMPT = """You are a local agent working one queued task.

Task id: {task_id}
Brief file: {brief}

Read the brief and do exactly what it asks, within the scope it allows. Do not
modify anything outside that scope. When you are done, reply with a short
report. The last part of your reply must contain the exact task id and exactly
one status line:

TASK_ID={task_id}
TASK_STATUS=COMPLETED

If a required part of the task could not be completed, use TASK_STATUS=BLOCKED
and list the blocker instead of claiming success. Do not create a report file
unless the brief explicitly requires an artifact; the worker saves your final
reply as report.md.
"""


class UniversalAdapter(runtime.Adapter):
    """Run one queued task through any CLI command template."""

    name = 'universal'
    display_name = 'Universal'
    raw_events = True

    def configure(self, parser):
        parser.add_argument('--agent', required=True,
                            help="queue worker id (lowercase, e.g. 'claude-code')")
        parser.add_argument('--command', required=True,
                            help='command template with an optional {prompt}, {task_id} or {brief} placeholder')
        parser.add_argument('--format', choices=('auto', 'jsonl', 'text'), default='auto',
                            help='how the agent reports: text, jsonl events, or auto (default)')
        parser.add_argument('--model-label', default=None,
                            help='model name recorded in process.json (informational only)')
        self.text = []

    def validate(self, parser, args):
        if not AGENT_ID.fullmatch(args.agent):
            parser.error("--agent must be a lowercase agent id such as 'claude-code'")
        if not shlex.split(args.command):
            parser.error('--command is empty')

    def prepare(self, args):
        self.args = args
        self.name = args.agent
        self.display_name = args.agent.upper()
        self.text = []

    def metadata(self, args):
        return {'command': args.command, 'format': args.format,
                'model_label': args.model_label}

    def build_prompt(self, args, task_id, brief):
        self.task_id = task_id
        self.brief = brief
        return PROMPT.format(task_id=task_id, brief=brief)

    def build_command(self, args, prompt):
        parts = [part.replace('{prompt}', prompt)
                      .replace('{task_id}', self.task_id)
                      .replace('{brief}', str(self.brief))
                 for part in shlex.split(args.command)]
        if '{prompt}' not in args.command and '{task_id}' not in args.command and '{brief}' not in args.command:
            parts.append(prompt)
        env = os.environ.copy()
        # The runner decodes stdout as UTF-8; tell Python-based CLIs to emit it.
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        return parts, env

    def parse_event(self, event):
        kind = event.get('type')
        if kind == 'raw':
            self.text.append(event.get('text', ''))
            return {'kind': 'text'}
        if self.args.format == 'text':
            self.text.append(json.dumps(event, ensure_ascii=False))
            return None
        return super().parse_event(event)

    def extract_report(self, state):
        final = state.get('final')
        if isinstance(final, dict) and isinstance(final.get('text'), str):
            return final['text']
        text = '\n'.join(line for line in self.text if line.strip())
        return text or None


if __name__ == '__main__':
    sys.exit(runtime.run(UniversalAdapter()))
