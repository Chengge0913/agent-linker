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



class ProgressViewTests(unittest.TestCase):
    def test_related_tasks_keep_group_after_completion(self):
        first = self.queue('publish', 'dsh', str(self.brief), '--group', 'collab-1')
        second = self.queue('publish', 'wb', str(self.brief), '--group', 'collab-1')
        self.queue('claim', 'dsh')
        self.queue('finish', 'dsh', first['id'], 'done')
        self.queue('claim', 'wb')
        self.queue('finish', 'wb', second['id'], 'done')
        rows = json.loads(self.snapshot().stdout)
        self.assertEqual([row['group_id'] for row in rows], ['collab-1', 'collab-1'])
        self.assertEqual([row['state'] for row in rows], ['delivered', 'delivered'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'tasks.sqlite'
        self.brief = self.base / 'brief.md'
        self.brief.write_text('test', encoding='utf-8')

    def queue(self, *args):
        output = subprocess.check_output([sys.executable, str(SRC / 'queue.py'), '--db', str(self.db), *args], text=True)
        return json.loads(output)

    def snapshot(self):
        return subprocess.run([sys.executable, str(SRC / 'progress.py'), '--db', str(self.db),
                               '--output-dir', str(self.base / 'out'), '--snapshot'],
                              capture_output=True, text=True, encoding='utf-8')

    def test_missing_database_is_reported_without_creating_it(self):
        result = self.snapshot()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('队列文件不存在', result.stderr)
        self.assertFalse(self.db.exists())

    def test_snapshot_combines_queue_status_with_latest_complete_progress_event(self):
        task = self.queue('publish', 'wb', str(self.brief))
        self.queue('claim', 'wb')
        progress = self.base / 'out' / task['id'] / 'progress.jsonl'
        progress.parent.mkdir(parents=True)
        progress.write_text(json.dumps({'task_id': task['id'], 'time': '2026-11-16T09:00:00+00:00',
                                        'stage': 'tool', 'tool': 'Read'}) + '\n{"partial":', encoding='utf-8')
        result = self.snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertEqual((row['id'], row['state'], row['progress']['tool']), (task['id'], 'running', 'Read'))

    def test_snapshot_never_overrides_delivered_state_with_stale_progress(self):
        task = self.queue('publish', 'wb', str(self.brief))
        self.queue('claim', 'wb')
        self.queue('finish', 'wb', task['id'], 'report.md')
        progress = self.base / 'out' / task['id'] / 'progress.jsonl'
        progress.parent.mkdir(parents=True)
        progress.write_text(json.dumps({'task_id': task['id'], 'stage': 'tool', 'tool': 'Write'}) + '\n', encoding='utf-8')
        result = self.snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]['state'], 'delivered')

    def test_snapshot_uses_brief_heading_as_readable_session_name(self):
        self.brief.write_text('# 审查测试结果\n\n请分析记录。', encoding='utf-8')
        self.queue('publish', 'wb', str(self.brief))
        result = self.snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]['name'], '审查测试结果')

    def test_snapshot_reports_model_from_worker_initialization_event(self):
        task = self.queue('publish', 'wb', str(self.brief))
        folder = self.base / 'out' / task['id']
        folder.mkdir(parents=True)
        event = {'type':'system', 'subtype':'init', 'model':'hy4std'}
        (folder / 'events.jsonl').write_text(json.dumps(event) + '\n', encoding='utf-8')
        result = self.snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]['model'], 'hy4std')

    def test_snapshot_includes_dsh_with_configured_model_labeled_as_configuration(self):
        task = self.queue('publish', 'dsh', str(self.brief))
        folder = self.base / 'out' / task['id']
        folder.mkdir(parents=True)
        (folder / 'process.json').write_text(json.dumps({'configured_model':'example-model'}), encoding='utf-8')
        result = self.snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertEqual((row['worker'], row['model'], row['model_source']), ('dsh', 'example-model', '配置'))

    def test_snapshot_exposes_recorded_start_and_terminal_timestamps(self):
        task = self.queue('publish', 'wb', str(self.brief))
        self.queue('claim', 'wb')
        self.queue('finish', 'wb', task['id'], 'report.md')
        folder = self.base / 'out' / task['id']
        folder.mkdir(parents=True)
        (folder / 'process.json').write_text(json.dumps({'started_at':'2026-11-16T09:00:00+00:00'}), encoding='utf-8')
        (folder / 'progress.jsonl').write_text(json.dumps({'task_id':task['id'], 'stage':'delivered', 'time':'2026-11-16T09:01:12+00:00'}) + '\n', encoding='utf-8')
        result = self.snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        row = json.loads(result.stdout)[0]
        self.assertEqual(row['started_at'], '2026-11-16T09:00:00+00:00')
        self.assertEqual(row['finished_at'], '2026-11-16T09:01:12+00:00')

    def test_terminal_task_with_only_nonterminal_progress_has_no_fabricated_finish(self):
        task = self.queue('publish', 'wb', str(self.brief))
        self.queue('claim', 'wb')
        self.queue('finish', 'wb', task['id'], 'recovered manually', '--blocked')
        folder = self.base / 'out' / task['id']
        folder.mkdir(parents=True)
        (folder / 'process.json').write_text(json.dumps({'started_at':'2026-11-16T09:00:00+00:00'}), encoding='utf-8')
        (folder / 'progress.jsonl').write_text(json.dumps({'task_id':task['id'], 'stage':'tool', 'time':'2026-11-16T09:01:00+00:00'}) + '\n', encoding='utf-8')
        row = json.loads(self.snapshot().stdout)[0]
        self.assertIsNone(row['finished_at'])


if __name__ == '__main__':
    unittest.main()
