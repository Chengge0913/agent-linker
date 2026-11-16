import sys
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path



class DashboardTests(unittest.TestCase):
    def test_server_can_reopen_previously_issued_loopback_url(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            db = base / 'queue.sqlite'
            subprocess.check_call([sys.executable, str(SRC / 'queue.py'), '--db', str(db), 'status'], stdout=subprocess.DEVNULL)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            expected = f'http://127.0.0.1:{port}/abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG/'
            url_file = base / 'url.txt'
            url_file.write_text(expected, encoding='utf-8')
            process = subprocess.Popen([sys.executable, str(SRC / 'dashboard.py'), '--db', str(db),
                                        '--output-dir', str(base / 'results'), '--url-file', str(url_file), '--reuse-url'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                result = None
                for _ in range(100):
                    try:
                        result = urllib.request.urlopen(expected + 'api/tasks', timeout=1).read()
                        break
                    except (urllib.error.URLError, OSError):
                        if process.poll() is not None:
                            break
                        time.sleep(.05)
                self.assertEqual(result, b'[]')
                self.assertEqual(url_file.read_text(encoding='utf-8'), expected)
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_server_exposes_only_existing_task_through_tokenized_url(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            db = base / 'queue.sqlite'
            brief = base / 'brief.md'
            brief.write_text('task', encoding='utf-8')
            output = subprocess.check_output([sys.executable, str(SRC / 'queue.py'), '--db', str(db),
                                              'publish', 'wb', str(brief)], text=True)
            task_id = json.loads(output)['id']
            url_file = base / 'url.txt'
            process = subprocess.Popen([sys.executable, str(SRC / 'dashboard.py'), '--db', str(db),
                                        '--output-dir', str(base / 'results'), '--url-file', str(url_file)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if url_file.is_file() and url_file.stat().st_size:
                        break
                    time.sleep(.05)
                url = url_file.read_text(encoding='utf-8').strip()
                tasks = json.loads(urllib.request.urlopen(url + 'api/tasks', timeout=2).read())
                self.assertEqual(tasks[0]['id'], task_id)
                page = urllib.request.urlopen(url, timeout=2).read().decode('utf-8')
                self.assertIn('协作时间线', page)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(url.rsplit('/', 2)[0] + '/wrong/api/tasks', timeout=2)
                self.assertEqual(denied.exception.code, 404)
                denied.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(url + 'api/task/' + 'a' * 32, timeout=2)
                self.assertEqual(missing.exception.code, 404)
                missing.exception.close()
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
