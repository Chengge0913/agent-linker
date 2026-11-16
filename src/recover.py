"""Inspect an interrupted worker task and conservatively mark it blocked."""
import argparse
import ctypes
import json
import os
import re
import sys
from pathlib import Path

from workers.wb import queue

WORKER_PATTERN = re.compile(r'^[a-z][a-z0-9-]{0,31}$')


def worker_id(value):
    if not WORKER_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError('worker must be a lowercase agent id such as wb, dsh or claude-code')
    return value


def pid_state(pid):
    if not isinstance(pid, int) or pid <= 0:
        return 'unknown'
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            return 'absent' if ctypes.get_last_error() == 87 else 'unknown'
        try:
            wait_result = kernel.WaitForSingleObject(handle, 0)
            return {0: 'absent', 258: 'active'}.get(wait_result, 'unknown')
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return 'active'
    except ProcessLookupError:
        return 'absent'
    except OSError:
        return 'unknown'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--worker', type=worker_id, default='wb',
                        help='agent id that owns the task, default wb')
    parser.add_argument('id')
    parser.add_argument('--confirmed-stopped', action='store_true',
                        help='After checking all processes and effects, mark the task blocked; never retries it')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{32}', args.id):
        parser.error('invalid task ID')
    tasks = [t for t in queue(args.db.resolve(), 'status') if t['id'] == args.id and t['worker'] == args.worker]
    if len(tasks) != 1:
        parser.error(f'{args.worker} task not found')
    task = tasks[0]
    folder = args.output_dir.resolve() / args.id
    try:
        metadata = json.loads((folder / 'process.json').read_text(encoding='utf-8'))
        if not isinstance(metadata, dict):
            metadata = {}
    except (OSError, ValueError):
        metadata = {}
    states = {key: pid_state(metadata.get(key)) for key in ('worker_pid', 'child_pid')}
    print(json.dumps({'task': task, 'process': metadata, 'pid_states': states,
                      'artifacts': str(folder)}, ensure_ascii=False), flush=True)
    if not args.confirmed_stopped:
        print('Inspect processes and artifacts; repeat with --confirmed-stopped only after confirming they stopped.', file=sys.stderr)
        return 1
    if task['state'] != 'running':
        print('Task is not running; no change made.', file=sys.stderr)
        return 1
    if 'active' in states.values():
        print('A recorded PID is active; no change made.', file=sys.stderr)
        return 1
    # Missing or reused PIDs and a crash between spawning and recording a child cannot
    # prove termination. Explicit operator confirmation is always required above.
    reason = f'Interrupted; operator confirmed processes stopped. Inspect artifacts: {folder}'
    queue(args.db.resolve(), 'finish', args.worker, args.id, reason, '--blocked')
    print(json.dumps({'id': args.id, 'state': 'blocked'}), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
