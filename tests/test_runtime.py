import sys
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path



class AnyAgentQueueTests(unittest.TestCase):
    """The ledger must accept any lowercase agent id, not just dsh/wb."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'queue.sqlite'
        self.brief = self.base / 'brief.md'
        self.brief.write_text('# 示例工单\n核对资料。\n', encoding='utf-8')

    def queue(self, *args):
        return subprocess.run([sys.executable, str(SRC / 'queue.py'), '--db', str(self.db), *args],
                              capture_output=True, text=True, encoding='utf-8')

    def test_any_agent_id_round_trips_through_the_ledger(self):
        published = json.loads(self.queue('publish', 'claude-code', str(self.brief)).stdout)
        claimed = json.loads(self.queue('claim', 'claude-code').stdout)
        self.assertEqual(claimed['id'], published['id'])
        finished = json.loads(self.queue('finish', 'claude-code', published['id'], 'done').stdout)
        self.assertEqual(finished['state'], 'delivered')

    def test_worker_id_rejects_path_like_names(self):
        result = self.queue('publish', '../escape', str(self.brief))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('agent id', result.stderr)

    def test_recover_accepts_any_agent_id(self):
        published = json.loads(self.queue('publish', 'echo', str(self.brief)).stdout)
        self.queue('claim', 'echo')
        result = subprocess.run([sys.executable, str(SRC / 'recover.py'), '--db', str(self.db),
                                 '--output-dir', str(self.base / 'out'), '--worker', 'echo',
                                 published['id'], '--confirmed-stopped'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.queue('status').stdout)[0]['state'], 'blocked')


class EchoWorkerTests(unittest.TestCase):
    """workers/echo.py is the reference adapter for the standard event protocol."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'queue.sqlite'
        self.brief = self.base / 'brief.md'
        self.brief.write_text('# 示例工单\n核对资料。\n', encoding='utf-8')

    def queue(self, *args):
        result = subprocess.run([sys.executable, str(SRC / 'queue.py'), '--db', str(self.db), *args],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        return json.loads(result.stdout)

    def run_echo(self, *extra):
        return subprocess.run([sys.executable, str(SRC / 'workers/echo.py'), '--db', str(self.db),
                               '--output-dir', str(self.base / 'out'), '--timeout', '30', *extra],
                              capture_output=True, text=True, encoding='utf-8')

    def test_standard_event_protocol_delivers_a_report(self):
        task = self.queue('publish', 'echo', str(self.brief))
        result = self.run_echo()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'delivered')
        folder = self.base / 'out' / task['id']
        report = (folder / 'report.md').read_text(encoding='utf-8')
        self.assertIn(f'TASK_ID={task["id"]}', report)
        self.assertIn('TASK_STATUS=COMPLETED', report)
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([event['type'] for event in events],
                         ['init', 'text', 'tool_call', 'tool_result', 'final'])
        metadata = json.loads((folder / 'process.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['agent'], 'echo')
        self.assertEqual(metadata['display_name'], 'Echo')
        progress = [json.loads(line) for line in (folder / 'progress.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertTrue(progress)
        self.assertTrue(all(event['worker'] == 'echo' for event in progress))
        self.assertIn('tool', result.stdout)

    def test_tool_error_blocks_even_if_the_agent_claims_completion(self):
        self.queue('publish', 'echo', str(self.brief))
        result = self.run_echo('--fail')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')

    def test_progress_snapshot_includes_any_agent(self):
        import progress
        task = self.queue('publish', 'echo', str(self.brief))
        self.queue('claim', 'echo')
        folder = self.base / 'out' / task['id']
        folder.mkdir(parents=True)
        (folder / 'progress.jsonl').write_text(
            json.dumps({'time': '2026-11-16T01:00:00+00:00', 'task_id': task['id'],
                        'stage': 'tool', 'worker': 'echo', 'tool': 'read'}) + '\n', encoding='utf-8')
        tasks = progress.snapshot(self.db, self.base / 'out')
        self.assertEqual(tasks[0]['worker'], 'echo')
        self.assertEqual(progress.worker_name(tasks[0]), 'ECHO')
        self.assertIn('读取资料', progress.describe(tasks[0]))


class UniversalWorkerTests(unittest.TestCase):
    """workers/universal.py lets any CLI agent join with a command template only."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'queue.sqlite'
        self.brief = self.base / 'brief.md'
        self.brief.write_text('# 示例工单\n核对资料。\n', encoding='utf-8')
        self.script = self.base / 'report.py'
        self.script.write_text(
            "import re, sys\n"
            "prompt = sys.argv[1]\n"
            "task_id = re.search(r'TASK_ID=([0-9a-f]{32})', prompt).group(1)\n"
            "print('核对完成：资料与源码一致。')\n"
            "print(f'TASK_ID={task_id}')\n"
            "print('TASK_STATUS=COMPLETED')\n",
            encoding='utf-8')

    def queue(self, *args):
        result = subprocess.run([sys.executable, str(SRC / 'queue.py'), '--db', str(self.db), *args],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        return json.loads(result.stdout)

    def run_universal(self, *extra):
        return subprocess.run([sys.executable, str(SRC / 'workers/universal.py'), '--db', str(self.db),
                               '--output-dir', str(self.base / 'out'), '--timeout', '30', *extra],
                              capture_output=True, text=True, encoding='utf-8')

    def test_text_format_delivers_a_report(self):
        task = self.queue('publish', 'external-demo', str(self.brief))
        command = f'"{Path(sys.executable).as_posix()}" "{self.script.as_posix()}" {{prompt}}'
        result = self.run_universal('--agent', 'external-demo', '--command', command, '--format', 'text')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'delivered')
        folder = self.base / 'out' / task['id']
        report = (folder / 'report.md').read_text(encoding='utf-8')
        self.assertIn(f'TASK_ID={task["id"]}', report)
        self.assertIn('TASK_STATUS=COMPLETED', report)
        self.assertIn('核对完成', report)
        metadata = json.loads((folder / 'process.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['agent'], 'external-demo')
        self.assertEqual(metadata['display_name'], 'EXTERNAL-DEMO')

    def test_auto_format_parses_standard_events(self):
        events_script = self.base / 'events.py'
        events_script.write_text(
            "import json, re, sys\n"
            "task_id = re.search(r'TASK_ID=([0-9a-f]{32})', sys.argv[1]).group(1)\n"
            "print(json.dumps({'type': 'init', 'model': 'external-1'}))\n"
            "print(json.dumps({'type': 'text', 'text': '开始核对'}))\n"
            "print(json.dumps({'type': 'final', 'text': f'TASK_ID={task_id}\\nTASK_STATUS=COMPLETED'}))\n",
            encoding='utf-8')
        task = self.queue('publish', 'event-agent', str(self.brief))
        command = f'"{Path(sys.executable).as_posix()}" "{events_script.as_posix()}" {{prompt}}'
        result = self.run_universal('--agent', 'event-agent', '--command', command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'delivered')
        folder = self.base / 'out' / task['id']
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([event['type'] for event in events], ['init', 'text', 'final'])

    def test_agent_id_is_validated(self):
        result = self.run_universal('--agent', '../escape', '--command', 'true')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('agent id', result.stderr)

    def test_missing_status_line_blocks(self):
        bare = self.base / 'bare.py'
        bare.write_text(
            "import re, sys\n"
            "task_id = re.search(r'TASK_ID=([0-9a-f]{32})', sys.argv[1]).group(1)\n"
            "print('TASK_ID=' + task_id)\n"
            "print('还没做完，但不写状态行。')\n",
            encoding='utf-8')
        self.queue('publish', 'bare-agent', str(self.brief))
        command = f'"{Path(sys.executable).as_posix()}" "{bare.as_posix()}" {{prompt}}'
        result = self.run_universal('--agent', 'bare-agent', '--command', command, '--format', 'text')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')


if __name__ == '__main__':
    unittest.main()