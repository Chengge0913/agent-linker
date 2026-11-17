"""Local task ledger. Any agent worker can claim tasks from the same queue."""

import argparse
import json
import re
import sqlite3
import uuid
from pathlib import Path

WORKER_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


def worker_id(value):
    if not WORKER_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "worker must be a lowercase agent id such as 'dsh', 'wb' or 'claude-code'"
        )
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("worker", type=worker_id, help="agent id that will run this task")
    publish.add_argument("brief", type=Path)
    publish.add_argument("--group", help="shared collaboration ID for related tasks")
    claim = commands.add_parser("claim")
    claim.add_argument("worker", type=worker_id)
    finish = commands.add_parser("finish")
    finish.add_argument("worker", type=worker_id)
    finish.add_argument("id")
    finish.add_argument("receipt")
    finish.add_argument("--blocked", action="store_true")
    commands.add_parser("status")
    args = parser.parse_args()

    with sqlite3.connect(args.db, timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                worker TEXT NOT NULL,
                brief TEXT NOT NULL,
                state TEXT NOT NULL,
                receipt TEXT
            )
        """)
        db.execute("CREATE TABLE IF NOT EXISTS task_groups (task_id TEXT PRIMARY KEY, group_id TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, worker TEXT NOT NULL, kind TEXT NOT NULL)")
        if args.command == "publish":
            brief = args.brief.resolve(strict=True)
            if not brief.is_file():
                parser.error("brief must be a file")
            task_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, 'pending', NULL)",
                (task_id, args.worker, str(brief)),
            )
            if args.group:
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', args.group):
                    parser.error('group must contain 1–64 letters, digits, hyphens or underscores')
                db.execute('INSERT INTO task_groups VALUES (?, ?)', (task_id, args.group))
            db.execute("INSERT INTO task_events (task_id, worker, kind) VALUES (?, ?, 'dispatch')", (task_id, args.worker))
            result = {"id": task_id, "state": "pending"}
        elif args.command == "claim":
            db.execute("BEGIN IMMEDIATE")
            task = db.execute(
                "SELECT * FROM tasks WHERE worker=? AND state='pending' ORDER BY rowid LIMIT 1",
                (args.worker,),
            ).fetchone()
            result = None
            if task:
                db.execute("UPDATE tasks SET state='running' WHERE id=?", (task["id"],))
                result = dict(task) | {"state": "running"}
        elif args.command == "finish":
            if not args.receipt.strip():
                parser.error("receipt must describe the result or blocker")
            state = "blocked" if args.blocked else "delivered"
            changed = db.execute(
                "UPDATE tasks SET state=?, receipt=? WHERE id=? AND worker=? AND state='running'",
                (state, args.receipt, args.id, args.worker),
            ).rowcount
            if changed != 1:
                parser.error("task must be running and assigned to this worker")
            db.execute("INSERT INTO task_events (task_id, worker, kind) VALUES (?, ?, ?)", (args.id, args.worker, 'return' if state == 'delivered' else 'blocked'))
            result = {"id": args.id, "state": state}
        else:
            result = [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY rowid")]

    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
