"""Supervise a local dashboard, restarting its own child at the same URL."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def acquire_lock(path):
    stream = path.open('a+b')
    stream.seek(0)
    if stream.read(1) == b'':
        stream.write(b'1')
        stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('another supervisor holds the status lock')
    return stream


def write_status(path, **fields):
    payload = {'time': datetime.now(timezone.utc).isoformat(), 'supervisor_pid': os.getpid(), **fields}
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def ready(url_file, child):
    try:
        url = url_file.read_text(encoding='utf-8').strip()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + 'api/tasks', timeout=1) as response:
            return child.poll() is None and response.status == 200
    except (OSError, ValueError, urllib.error.URLError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--url-file', required=True, type=Path)
    parser.add_argument('--status-file', required=True, type=Path)
    parser.add_argument('--log-file', type=Path)
    parser.add_argument('--retry-delay', type=float, default=1.0)
    parser.add_argument('--max-restarts', type=int, default=3)
    args = parser.parse_args()
    if args.retry_delay <= 0 or args.max_restarts < 0:
        parser.error('retry delay must be positive and max restarts nonnegative')
    for path in (args.db, args.output_dir, args.url_file, args.status_file):
        if not path.is_absolute():
            parser.error('all paths must be absolute')
    if not args.db.is_file():
        parser.error('database does not exist')
    args.status_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock = acquire_lock(args.status_file.with_suffix(args.status_file.suffix + '.lock'))
    except (OSError, RuntimeError) as exc:
        parser.error(str(exc))
    log_path = args.log_file or args.status_file.with_suffix('.log')
    child = None
    restarts = []
    try:
        with log_path.open('a', encoding='utf-8') as log:
            while True:
                command = [sys.executable, str(HERE / 'dashboard.py'), '--db', str(args.db),
                           '--output-dir', str(args.output_dir), '--url-file', str(args.url_file)]
                if args.url_file.is_file():
                    command.append('--reuse-url')
                flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
                child = subprocess.Popen(command, stdout=log, stderr=log, creationflags=flags)
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline and child.poll() is None and not ready(args.url_file, child):
                    time.sleep(.1)
                if child.poll() is None and ready(args.url_file, child):
                    write_status(args.status_file, state='running', child_pid=child.pid, restart_count=len(restarts))
                    exit_code = child.wait()
                    reason = f'dashboard exited with code {exit_code}'
                else:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)
                    reason = f'dashboard could not start (exit code {child.returncode})'
                log.write(f'{datetime.now(timezone.utc).isoformat()} {reason}\n')
                log.flush()
                restarts = [stamp for stamp in restarts if time.monotonic() - stamp < 60]
                if len(restarts) >= args.max_restarts:
                    write_status(args.status_file, state='failed', child_pid=None, restart_count=len(restarts), reason=reason)
                    return 1
                restarts.append(time.monotonic())
                write_status(args.status_file, state='restarting', child_pid=None, restart_count=len(restarts), reason=reason)
                time.sleep(args.retry_delay)
    except KeyboardInterrupt:
        return 130
    finally:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        lock.close()


if __name__ == '__main__':
    sys.exit(main())
