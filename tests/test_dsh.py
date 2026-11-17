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



class DshWorkerTests(unittest.TestCase):
    def test_cli_discovery_uses_installed_path_without_environment(self):
        sys.path.insert(0, str(SRC / 'workers'))
        from dsh import discover_cli
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / 'DeepSeek Harness.exe'
            installed.touch()
            self.assertEqual(discover_cli({}, [installed]), installed)
            self.assertIsNone(discover_cli({}, [installed.parent / 'missing.exe']))
            self.assertEqual(discover_cli({'DSH_CLI': 'custom.exe'}, [installed]), Path('custom.exe'))

    def test_model_route_reads_simple_overlay_and_rejects_missing_route(self):
        sys.path.insert(0, str(SRC / 'workers'))
        from dsh import overlay_model_route
        with tempfile.TemporaryDirectory() as directory:
            patch = Path(directory) / 'overlay.yml'
            patch.write_text('- id: agent-default-model\n  config:\n    provider: qwen-token-plan-cn\n    model: deepseek-v4.1-flash\n', encoding='utf-8')
            self.assertEqual(overlay_model_route(patch), ('qwen-token-plan-cn', 'deepseek-v4.1-flash'))
            patch.write_text('- id: something-else\n  config:\n    model: other\n', encoding='utf-8')
            self.assertIsNone(overlay_model_route(patch))

    def test_electron_asar_entrypoint_is_accepted_when_archive_exists(self):
        sys.path.insert(0, str(SRC / 'workers'))
        from dsh import valid_entrypoint
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'app.asar'
            entry = archive / 'dsh' / 'cli.js'
            self.assertFalse(valid_entrypoint(entry))
            archive.write_bytes(b'archive')
            self.assertTrue(valid_entrypoint(entry))
            self.assertFalse(valid_entrypoint(Path(directory) / 'missing.js'))
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'queue.sqlite'
        self.brief = self.base / 'brief.md'
        self.brief.write_text('# 只读测试\n读取资料。', encoding='utf-8')
        self.patch = self.base / 'overlay.yml'
        self.patch.write_text('[]', encoding='utf-8')
        self.fake = self.base / 'fake.py'

    def queue(self, *args):
        result = subprocess.run([sys.executable, str(SRC / 'queue.py'), '--db', str(self.db), *args],
                                capture_output=True, text=True, encoding='utf-8', check=True)
        return json.loads(result.stdout)

    def run_worker(self, code, *extra):
        self.fake.write_text(code, encoding='utf-8')
        return subprocess.run([sys.executable, str(SRC / 'workers/dsh.py'), '--db', str(self.db),
                               '--output-dir', str(self.base / 'out'), '--cli', sys.executable,
                               '--entrypoint', str(self.fake), '--patch', str(self.patch),
                               '--model-label', 'example-model', '--timeout', '10', *extra],
                              capture_output=True, text=True, encoding='utf-8')

    def test_success_claims_only_dsh_and_persists_result(self):
        self.queue('publish', 'wb', str(self.brief))
        task = self.queue('publish', 'dsh', str(self.brief))
        code = """import json,sys\na=sys.argv\np=a[-1]\ntask=p.split('TASK_ID=')[1].split()[0]\nassert a[a.index('--permission-mode')+1]=='read-only'\nprint(json.dumps({'type':'session','sessionId':'example'}),flush=True)\nprint(json.dumps({'type':'tool_call','tool':'read','callId':'c1','input':{'file_path':'brief.md'}}),flush=True)\nprint(json.dumps({'type':'final','text':'TASK_ID='+task+'\\nTASK_STATUS=COMPLETED\\nDone.'}),flush=True)\n"""
        result = self.run_worker(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        tasks = self.queue('status')
        self.assertEqual(tasks[0]['state'], 'pending')
        self.assertEqual(tasks[1]['state'], 'delivered')
        self.assertIn(task['id'], Path(tasks[1]['receipt']).read_text(encoding='utf-8'))
        folder = self.base / 'out' / task['id']
        self.assertTrue((folder / 'events.raw.jsonl').is_file())
        self.assertEqual(json.loads((folder / 'process.json').read_text())['configured_model'], 'example-model')
        self.assertIn('tool', result.stdout)

    def test_failed_tool_event_cannot_be_reported_as_delivered(self):
        self.queue('publish', 'dsh', str(self.brief))
        code = """import json,sys\ntask=sys.argv[-1].split('TASK_ID=')[1].split()[0]\nprint(json.dumps({'type':'tool_result','status':'failed','callId':'c1','result':'denied'}))\nprint(json.dumps({'type':'final','text':'TASK_ID='+task+'\\nTASK_STATUS=COMPLETED'}))\n"""
        result = self.run_worker(code)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')

    def test_mismatched_id_cannot_be_delivered(self):
        self.queue('publish', 'dsh', str(self.brief))
        result = self.run_worker("import json\nprint(json.dumps({'type':'final','text':'TASK_STATUS=COMPLETED\\nwrong id'}))")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')

    def test_workspace_write_is_opt_in_and_recorded(self):
        task = self.queue('publish', 'dsh', str(self.brief))
        code = """import json,sys\na=sys.argv\nassert a[a.index('--permission-mode')+1]=='workspace-write'\np=a[-1]\ntask=p.split('TASK_ID=')[1].split()[0]\nprint(json.dumps({'type':'final','text':'TASK_ID='+task+'\\nTASK_STATUS=COMPLETED'}),flush=True)\n"""
        result = self.run_worker(code, '--permission-mode', 'workspace-write')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'delivered')
        metadata = json.loads((self.base / 'out' / task['id'] / 'process.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['permission_mode'], 'workspace-write')

    def test_confirmed_interrupted_dsh_task_can_be_marked_blocked(self):
        task = self.queue('publish', 'dsh', str(self.brief))
        self.queue('claim', 'dsh')
        result = subprocess.run([sys.executable, str(SRC / 'recover.py'), '--db', str(self.db),
                                 '--output-dir', str(self.base / 'out'), '--worker', 'dsh', task['id'], '--confirmed-stopped'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.queue('status')[0]['state'], 'blocked')


if __name__ == '__main__':
    unittest.main()
