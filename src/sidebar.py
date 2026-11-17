"""Start or reuse the local activity dashboard and open it in a browser."""
import argparse
import hashlib
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent


def served(url):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + 'api/tasks', timeout=1) as response:
            return response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def stored_url(path):
    try:
        return path.read_text(encoding='utf-8').strip() or None
    except OSError:
        return None


def ensure_sidebar(db=None, output_dir=None, url_file=None, status_file=None,
                   wait=20.0, open_browser=True):
    """Return the live dashboard URL, starting the supervisor when needed."""
    db = Path(db or HERE / 'tasks.sqlite').resolve()
    output_dir = Path(output_dir or HERE / 'results').resolve()
    key = hashlib.sha256(f'{db}\0{output_dir}'.encode('utf-8')).hexdigest()[:16]
    runtime_dir = Path.home() / '.agent-linker' / 'dashboards'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    url_file = Path(url_file or runtime_dir / f'{key}-url.txt').resolve()
    status_file = Path(status_file or runtime_dir / f'{key}-status.json').resolve()
    url = stored_url(url_file)
    if not (url and served(url)):
        command = [sys.executable, str(HERE / 'dashboard_supervisor.py'),
                   '--db', str(db), '--output-dir', str(output_dir),
                   '--url-file', str(url_file), '--status-file', str(status_file)]
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            url = stored_url(url_file)
            if url and served(url):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f'dashboard did not become ready within {wait}s')
    if open_browser:
        webbrowser.open(url)
    return url


def start_hud(db, output_dir):
    executable = Path(sys.executable)
    windowed = executable.with_name('pythonw.exe')
    if os.name == 'nt' and windowed.is_file():
        executable = windowed
    command = [str(executable), str(HERE / 'hud.py'), '--db', str(Path(db).resolve()),
               '--output-dir', str(Path(output_dir).resolve())]
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=HERE / 'tasks.sqlite')
    parser.add_argument('--output-dir', type=Path, default=HERE / 'results')
    parser.add_argument('--url-file', type=Path)
    parser.add_argument('--status-file', type=Path)
    parser.add_argument('--wait', type=float, default=20.0, help='seconds to wait for the dashboard')
    parser.add_argument('--hud', action='store_true', help='start the floating progress window')
    parser.add_argument('--no-open', dest='open_browser', action='store_false')
    parser.set_defaults(open_browser=True)
    args = parser.parse_args()
    if args.wait <= 0:
        parser.error('wait must be positive')
    try:
        url = ensure_sidebar(db=args.db, output_dir=args.output_dir, url_file=args.url_file,
                             status_file=args.status_file, wait=args.wait, open_browser=args.open_browser)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.hud:
        start_hud(args.db, args.output_dir)
    print(url)
    return 0


if __name__ == '__main__':
    sys.exit(main())
