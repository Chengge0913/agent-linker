import sys
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path



class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.db = self.path / 'queue.sqlite'
        self.brief = self.path / 'brief.md'
        self.brief.write_text('Read this task.', encoding='utf-8')
        self.fake = self.path / 'fake_cli.py'

    def queue(self, *args):
        result = subprocess.run([sys.executable, str(SRC / 'queue.py'), '--db', str(self.db), *args],
                                text=True, capture_output=True, check=True, encoding='utf-8')
        return json.loads(result.stdout)

    def run_worker(self, program, *extra):
        self.fake.write_text(program, encoding='utf-8')
        return subprocess.run([sys.executable, str(SRC / 'workers/wb.py'), '--db', str(self.db),
                               '--output-dir', str(self.path / 'out'), '--cli', sys.executable,
                               '--cli-prefix', str(self.fake), '--timeout', '10', *extra],
                              text=True, capture_output=True, encoding='utf-8')

    def test_success_records_worker_result_and_leaves_other_role_pending(self):
        self.queue('publish', 'dsh', str(self.brief))
        task = self.queue('publish', 'wb', str(self.brief))
        code = """import json, sys\nprint(json.dumps({'type':'system','subtype':'init'}), flush=True)\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'TASK_STATUS=COMPLETED\\nTask %s completed with evidence.' % sys.argv[-1].split('TASK_ID=')[1].split()[0]}), flush=True)\n"""
        result = self.run_worker(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        tasks = self.queue('status')
        self.assertEqual(tasks[0]['state'], 'pending')
        self.assertEqual(tasks[1]['state'], 'delivered')
        self.assertIn(task['id'], Path(tasks[1]['receipt']).read_text(encoding='utf-8'))
        self.assertIn('init', result.stdout)
        self.assertTrue((self.path / 'out' / task['id'] / 'events.jsonl').is_file())
        progress = [json.loads(line) for line in (self.path / 'out' / task['id'] / 'progress.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(progress[0]['stage'], 'claimed')
        self.assertEqual(progress[-1]['stage'], 'delivered')
        self.assertTrue(all(item.get('time') for item in progress))

    def test_missing_matching_result_is_blocked(self):
        self.queue('publish', 'wb', str(self.brief))
        result = self.run_worker("import json\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'wrong task'}))\n")
        self.assertNotEqual(result.returncode, 0)
        task = self.queue('status')[0]
        self.assertEqual(task['state'], 'blocked')
        self.assertTrue(task['receipt'])

    def test_nonzero_exit_cannot_deliver_even_with_success_event(self):
        self.queue('publish', 'wb', str(self.brief))
        result = self.run_worker("import json,sys\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':sys.argv[-1]}))\nsys.exit(3)\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')

    def test_empty_queue_does_not_start_cli(self):
        result = self.run_worker("raise Exception('must not run')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.queue('status'), [])

    def test_edits_are_enabled_only_when_explicitly_requested(self):
        self.queue('publish', 'wb', str(self.brief))
        fake = """import json,sys\na=sys.argv\ntools=a[a.index('--tools')+1]\nprompt=a[-1]\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'TASK_STATUS=COMPLETED\\n'+prompt+' TOOLS='+tools}))\n"""
        result = self.run_worker(fake, '--allow-edits')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = Path(self.queue('status')[0]['receipt']).read_text(encoding='utf-8')
        self.assertIn('TOOLS=Read,Write,Edit', report)
        self.assertNotIn('authorized read-only work', report)
        self.assertIn('brief public progress', report)

    def test_worker_reported_blocker_is_not_marked_delivered(self):
        self.queue('publish', 'wb', str(self.brief))
        fake = """import json,sys\ntask=sys.argv[-1].split('TASK_ID=')[1].split()[0]\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'TASK_ID='+task+'\\nTASK_STATUS=BLOCKED\\nWrite was denied'}))\n"""
        result = self.run_worker(fake)
        self.assertNotEqual(result.returncode, 0)
        task = self.queue('status')[0]
        self.assertEqual(task['state'], 'blocked')
        self.assertIn('Write was denied', Path(task['receipt']).read_text(encoding='utf-8'))

    def test_permission_denial_cannot_be_marked_delivered_by_self_report(self):
        self.queue('publish', 'wb', str(self.brief))
        fake = """import json,sys\ntask=sys.argv[-1].split('TASK_ID=')[1].split()[0]\nprint(json.dumps({'type':'user','message':{'content':[{'type':'tool_result','is_error':True,'content':[{'type':'text','text':'Permission to use Bash has been denied'}]}]}}))\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'TASK_ID='+task+'\\nTASK_STATUS=COMPLETED'}))\n"""
        result = self.run_worker(fake)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')

    def test_command_mode_only_allows_explicit_bash_command(self):
        self.queue('publish', 'wb', str(self.brief))
        fake = """import json,sys\na=sys.argv\ntask=a[-1].split('TASK_ID=')[1].split()[0]\nsettings=json.loads(a[a.index('--settings')+1])\nprint(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'TASK_STATUS=COMPLETED\\nTASK_ID='+task+'\\nMODEL='+a[a.index('--model')+1]+'\\nTOOLS='+a[a.index('--tools')+1]+'\\nALLOWED='+a[a.index('--allowedTools')+1]+'\\nSETTINGS='+json.dumps(settings)+'\\nPROMPT='+a[-1]}))\n"""
        result = self.run_worker(fake, '--allow-bash-command', 'python --version')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = Path(self.queue('status')[0]['receipt']).read_text(encoding='utf-8')
        self.assertIn('MODEL=glm-5.3', report)
        self.assertIn('TOOLS=Read,Bash', report)
        self.assertIn('Bash(python --version)', report)
        self.assertIn('"allow": ["Bash(python --version)"]', report)
        self.assertIn('Do not append', report)
        self.assertNotIn('Bash(*)', report)

    def test_command_rule_rejects_wildcards_before_claiming(self):
        self.queue('publish', 'wb', str(self.brief))
        result = self.run_worker('raise RuntimeError()', '--allow-bash-command', 'python *')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('wildcards', result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'pending')

    def test_timeout_marks_claimed_task_blocked(self):
        self.queue('publish', 'wb', str(self.brief))
        result = self.run_worker('import time\ntime.sleep(5)\n', '--timeout', '1')
        self.assertNotEqual(result.returncode, 0)
        task = self.queue('status')[0]
        self.assertEqual(task['state'], 'blocked')
        self.assertIn('timed out', task['receipt'])

    def test_recovery_refuses_live_worker_even_after_confirmation(self):
        task = self.queue('publish', 'wb', str(self.brief))
        self.queue('claim', 'wb')
        folder = self.path / 'out' / task['id']
        folder.mkdir(parents=True)
        (folder / 'process.json').write_text(json.dumps({'worker_pid': os.getpid(), 'child_pid': None}), encoding='utf-8')
        result = subprocess.run([sys.executable, str(SRC / 'recover.py'), '--db', str(self.db),
                                 '--output-dir', str(self.path / 'out'), task['id'], '--confirmed-stopped'],
                                text=True, capture_output=True, encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('active', result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'running')

    def test_recovery_requires_confirmation_when_process_info_is_missing(self):
        task = self.queue('publish', 'wb', str(self.brief))
        self.queue('claim', 'wb')
        command = [sys.executable, str(SRC / 'recover.py'), '--db', str(self.db),
                   '--output-dir', str(self.path / 'out'), task['id']]
        result = subprocess.run(command, text=True, capture_output=True, encoding='utf-8')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue('status')[0]['state'], 'running')
        result = subprocess.run(command + ['--confirmed-stopped'], text=True, capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')


if __name__ == '__main__':
    unittest.main()
