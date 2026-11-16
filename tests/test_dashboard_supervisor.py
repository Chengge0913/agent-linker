import sys
from pathlib import Path
SRC = Path(__file__).resolve().parent.parent / 'src'
sys.path.insert(0, str(SRC))
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from recover import pid_state



def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.1)
    return None


class SupervisorTests(unittest.TestCase):
    def test_occupied_port_stops_after_bounded_retries(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as listener:
            base = Path(directory)
            db, urls, status = (base / name for name in ('tasks.sqlite', 'url.txt', 'status.json'))
            subprocess.check_call([sys.executable, str(SRC / 'queue.py'), '--db', str(db), 'status'], stdout=subprocess.DEVNULL)
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            urls.write_text(f'http://127.0.0.1:{listener.getsockname()[1]}/abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG/', encoding='utf-8')
            result = subprocess.run([sys.executable, str(SRC / 'dashboard_supervisor.py'), '--db', str(db),
                                     '--output-dir', str(base / 'results'), '--url-file', str(urls),
                                     '--status-file', str(status), '--retry-delay', '0.2', '--max-restarts', '1'],
                                    capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            info = json.loads(status.read_text(encoding='utf-8'))
            self.assertEqual(info['state'], 'failed')
            self.assertEqual(info['restart_count'], 1)
            self.assertNotEqual(listener.fileno(), -1)

    def test_unexpected_child_exit_restarts_same_url(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            db, urls, status, results = (base / name for name in ('tasks.sqlite', 'url.txt', 'status.json', 'results'))
            subprocess.check_call([sys.executable, str(SRC / 'queue.py'), '--db', str(db), 'status'], stdout=subprocess.DEVNULL)
            process = subprocess.Popen([sys.executable, str(SRC / 'dashboard_supervisor.py'), '--db', str(db),
                                        '--output-dir', str(results), '--url-file', str(urls), '--status-file', str(status),
                                        '--retry-delay', '0.2'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            child = None
            try:
                info = wait_for(lambda: json.loads(status.read_text(encoding='utf-8')) if status.exists() else None)
                self.assertIsNotNone(info)
                self.assertEqual(info['state'], 'running')
                child = info['child_pid']
                url = urls.read_text(encoding='utf-8').strip()
                self.assertEqual(json.loads(urllib.request.urlopen(url + 'api/tasks', timeout=2).read()), [])
                duplicate = subprocess.run([sys.executable, str(SRC / 'dashboard_supervisor.py'), '--db', str(db),
                                            '--output-dir', str(results), '--url-file', str(urls), '--status-file', str(status)],
                                           capture_output=True, timeout=5)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertIsNone(process.poll())
                os.kill(child, signal.SIGTERM)
                recovered = wait_for(lambda: (lambda s: s if s['state']=='running' and s['child_pid']!=child else None)(json.loads(status.read_text(encoding='utf-8'))))
                self.assertIsNotNone(recovered, 'dashboard did not restart')
                child = recovered['child_pid']
                self.assertEqual(urls.read_text(encoding='utf-8').strip(), url)
                self.assertEqual(json.loads(urllib.request.urlopen(url + 'api/tasks', timeout=2).read()), [])
                self.assertEqual(recovered['restart_count'], 1)
            finally:
                process.terminate()
                process.wait(timeout=5)
                if child:
                    try:
                        os.kill(child, signal.SIGTERM)
                        wait_for(lambda: pid_state(child) == 'absent', timeout=5)
                    except OSError:
                        pass


if __name__ == '__main__':
    unittest.main()
