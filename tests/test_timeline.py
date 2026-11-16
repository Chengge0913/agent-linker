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



class TimelineTests(unittest.TestCase):
    def test_timeline_renders_public_text_and_tools_without_internal_thinking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            events = [
                {'type': 'assistant', '__timestamp': '2026-11-16T09:00:00Z', 'message': {'content': [
                    {'type': 'thinking', 'thinking': 'PRIVATE-THINKING'},
                    {'type': 'text', 'text': '先核对输入文件。'},
                    {'type': 'tool_use', 'name': 'Read', 'input': {'file_path': 'brief.md'}}]}},
                {'type': 'user', 'message': {'content': [
                    {'type': 'tool_result', 'is_error': False, 'content': [{'type': 'text', 'text': '文件内容'}]}]}},
            ]
            path.write_text('\n'.join(json.dumps(event, ensure_ascii=False) for event in events) + '\n{"partial":', encoding='utf-8')
            result = subprocess.run([sys.executable, str(SRC / 'timeline.py'), str(path)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            text = result.stdout
            self.assertIn('先核对输入文件', text)
            self.assertIn('brief.md', text)
            self.assertIn('文件内容', text)
            self.assertNotIn('PRIVATE-THINKING', text)

    def test_large_tool_response_is_capped_with_explicit_notice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            event = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'is_error': False,
                     'content': [{'type': 'text', 'text': 'X' * 15000}]}]}}
            path.write_text(json.dumps(event), encoding='utf-8')
            result = subprocess.run([sys.executable, str(SRC / 'timeline.py'), str(path)],
                                    capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLess(len(result.stdout), 10000)
            self.assertIn('截断', result.stdout)

    def test_tool_calls_and_results_include_matching_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            events = [{'type': 'assistant', 'message': {'content': [{'type':'tool_use', 'id':'call-1', 'name':'Read', 'input':{}}]}},
                      {'type': 'user', 'message': {'content': [{'type':'tool_result', 'tool_use_id':'call-1', 'content':'ok'}]}}]
            path.write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
            result = subprocess.run([sys.executable, str(SRC / 'timeline.py'), str(path)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            items = json.loads(result.stdout)
            self.assertEqual([item['call_id'] for item in items], ['call-1', 'call-1'])

    def test_dsh_events_render_public_text_and_paired_tool_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            events = [{'type':'tool_call', 'tool':'read', 'callId':'c1', 'input':{'file_path':'brief.md'}},
                      {'type':'tool_result', 'callId':'c1', 'status':'completed', 'result':'file contents'},
                      {'type':'text', 'text':'执行说明'}]
            path.write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in events), encoding='utf-8')
            result = subprocess.run([sys.executable, str(SRC / 'timeline.py'), str(path)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            items = json.loads(result.stdout)
            self.assertEqual([item['kind'] for item in items], ['tool','result','message'])
            self.assertEqual(items[0]['call_id'], items[1]['call_id'])


if __name__ == '__main__':
    unittest.main()
