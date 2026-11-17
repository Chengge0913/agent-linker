"""Read-only desktop progress window for a local agent queue."""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

KNOWN_NAMES = {}


def brief_name(brief):
    path = Path(brief)
    if path.name.lower() in KNOWN_NAMES:
        return KNOWN_NAMES[path.name.lower()]
    try:
        with path.open(encoding='utf-8-sig') as stream:
            for _ in range(15):
                line = stream.readline(2048)
                if not line:
                    break
                if line.startswith('# '):
                    title = line[2:].strip()
                    if title:
                        return title[:60]
    except (OSError, UnicodeError):
        pass
    return path.stem.replace('_', ' ').replace('-', ' ')[:60] or '未命名任务'


def last_progress(path, task_id):
    try:
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 65536))
            lines = stream.read().decode('utf-8', errors='replace').splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            item = json.loads(line)
            if isinstance(item, dict) and item.get('task_id') == task_id:
                return item
        except (ValueError, TypeError):
            continue
    return None


def actual_model(path):
    try:
        with path.open(encoding='utf-8') as stream:
            for _ in range(100):
                line = stream.readline(500_000)
                if not line:
                    break
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict) and (
                        (event.get('type') == 'system' and event.get('subtype') == 'init')
                        or event.get('type') == 'init'):
                    model = event.get('model')
                    return model if isinstance(model, str) and model else None
    except (OSError, UnicodeError):
        pass
    return None


def snapshot(db, output_dir):
    if not db.is_file():
        raise FileNotFoundError(f'队列文件不存在：{db}')
    connection = sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        connection.row_factory = sqlite3.Row
        tasks = [dict(row) for row in connection.execute(
            "SELECT id, worker, brief, state, receipt FROM tasks ORDER BY rowid DESC LIMIT 30")]
        has_groups = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_groups'").fetchone()
        groups = dict(connection.execute('SELECT task_id, group_id FROM task_groups')) if has_groups else {}
        for task in tasks:
            task['group_id'] = groups.get(task['id'])
    finally:
        connection.close()
    for task in tasks:
        task['name'] = brief_name(task['brief'])
        folder = output_dir / task['id']
        task['model'] = actual_model(folder / 'events.jsonl')
        task['model_source'] = '执行端记录' if task['model'] else None
        task['progress'] = last_progress(folder / 'progress.jsonl', task['id'])
        try:
            metadata = json.loads((folder / 'process.json').read_text(encoding='utf-8'))
            task['started_at'] = metadata.get('started_at') if isinstance(metadata, dict) else None
            if not task['model'] and isinstance(metadata, dict) and isinstance(metadata.get('configured_model'), str):
                task['model'] = metadata['configured_model']
                task['model_source'] = '配置'
        except (OSError, UnicodeError, ValueError):
            task['started_at'] = None
        progress = task['progress'] or {}
        task['finished_at'] = progress.get('time') if progress.get('stage') == task['state'] and task['state'] in ('delivered', 'blocked') else None
    return tasks


STATES = {'pending': '等待执行', 'running': '执行中', 'delivered': '已返回 · 待审查', 'blocked': '遇到阻塞'}
STAGES = {'claimed': '已认领工单', 'init': '执行端已启动', 'result_received': '收到执行端回执',
          'delivered': '报告已返回，等待主控审查', 'blocked': '执行中断或遇到阻塞'}
WORKER_NAMES = {'wb': 'WB', 'dsh': 'DSH'}
TOOL_LABELS = {'read': '读取资料', 'write': '写入文件', 'edit': '编辑文件', 'bash': '运行命令',
               'glob': '查找文件', 'grep': '搜索内容', 'list': '浏览目录', 'web_fetch': '抓取网页',
               'web_search': '搜索网络', 'todo_write': '更新任务清单'}


def tool_label(name):
    name = str(name or '')
    return TOOL_LABELS.get(name.lower(), name or '未知')


def worker_name(task):
    worker = task.get('worker') or ''
    return WORKER_NAMES.get(worker, worker.upper() or '未知执行端')


def describe(task):
    state = task['state']
    if state == 'pending':
        return '工单已派发，等待工作进程认领。'
    if state == 'delivered':
        return '执行端已返回报告；主控仍需核对结果。'
    if state == 'blocked':
        return '工单已阻塞，请查看报告或阻塞原因。'
    event = task.get('progress') or {}
    if event.get('stage') == 'tool':
        return f"最近调用工具：{tool_label(event.get('tool'))}"
    return STAGES.get(event.get('stage'), '已认领，等待执行进度。')


def local_time(value):
    try:
        return datetime.fromisoformat(value).astimezone().strftime('%m-%d %H:%M:%S')
    except (TypeError, ValueError):
        return '暂无事件时间'


def show_window(db, output_dir):
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title('协作进度')
    root.geometry('480x470')
    root.minsize(420, 390)
    root.configure(bg='#f4f7fb')
    root.attributes('-topmost', True)
    font = ('Microsoft YaHei UI', 10)
    header = tk.Frame(root, bg='#173d64', padx=18, pady=13)
    header.pack(fill='x')
    tk.Label(header, text='✈  协作进度', bg='#173d64', fg='white',
             font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w')
    tk.Label(header, text='任意 Agent · 本地队列', bg='#173d64', fg='#c9dfef', font=font).pack(anchor='w')
    body = tk.Frame(root, bg='#f4f7fb', padx=15, pady=10)
    body.pack(fill='both', expand=True)
    pinned = tk.BooleanVar(value=True)
    toolbar = tk.Frame(body, bg='#f4f7fb')
    toolbar.pack(fill='x', pady=(0, 5))
    tk.Checkbutton(toolbar, text='窗口置顶', variable=pinned, bg='#f4f7fb', font=font,
                   command=lambda: root.attributes('-topmost', pinned.get())).pack(side='right')
    tk.Button(toolbar, text='打开任务目录', font=font, command=lambda: open_folder(), relief='flat',
              bg='#dcecf8', fg='#173d64', padx=10).pack(side='left')
    listing = tk.Listbox(body, height=5, font=font, activestyle='none', exportselection=False,
                         bg='white', fg='#203852', selectbackground='#d8eafa', selectforeground='#173d64',
                         relief='flat', highlightthickness=1, highlightbackground='#dce6ef')
    listing.pack(fill='x')
    card = tk.Frame(body, bg='white', padx=12, pady=10, highlightthickness=1, highlightbackground='#dce6ef')
    card.pack(fill='both', expand=True, pady=8)
    title = tk.Label(card, text='等待工单', bg='white', fg='#173d64', font=('Microsoft YaHei UI', 12, 'bold'), anchor='w')
    title.pack(fill='x')
    detail = tk.Label(card, text='', bg='white', fg='#334d64', font=font, anchor='nw', justify='left', wraplength=410)
    detail.pack(fill='both', expand=True, pady=(6, 0))
    footer = tk.Label(body, text='', bg='#f4f7fb', fg='#60778a', font=('Microsoft YaHei UI', 9), anchor='w')
    footer.pack(fill='x')
    rows = []
    selected_id = None
    follow_latest = True

    def render():
        current = listing.curselection()
        if not current or current[0] >= len(rows):
            title.config(text='等待工单')
            detail.config(text='队列中暂无工单。')
            return
        task = rows[current[0]]
        title.config(text=STATES.get(task['state'], task['state']))
        event = task.get('progress') or {}
        receipt = task.get('receipt') or '尚无回执'
        detail.config(text=f"{describe(task)}\n\n执行端：{worker_name(task)}\n模型：{task.get('model') or '未记录'}\n工单：{task['id']}\n工单文件：{Path(task['brief']).name}\n"
                           f"最近进度：{local_time(event.get('time'))}\n回执：{receipt}")

    def selected(_event):
        nonlocal selected_id, follow_latest
        current = listing.curselection()
        if current and current[0] < len(rows):
            selected_id = rows[current[0]]['id']
            follow_latest = current[0] == 0
        render()

    listing.bind('<<ListboxSelect>>', selected)

    def refresh():
        nonlocal rows, selected_id
        try:
            rows = snapshot(db, output_dir)
            listing.delete(0, tk.END)
            for task in rows:
                listing.insert(tk.END, f"[{worker_name(task)}] {STATES.get(task['state'], task['state'])}    {Path(task['brief']).name}    {task['id'][:8]}")
            if rows:
                index = 0 if follow_latest else next((i for i, task in enumerate(rows) if task['id'] == selected_id), 0)
                selected_id = rows[index]['id']
                listing.selection_set(index)
            render()
            footer.config(text=f"每 1 秒刷新 · 队列：{db.name}", fg='#60778a')
        except (OSError, sqlite3.Error) as exc:
            title.config(text='暂时无法读取队列')
            detail.config(text=str(exc))
            footer.config(text='将继续重试；请检查数据库路径。', fg='#ae4a3f')
        root.after(1000, refresh)

    def open_folder():
        current = listing.curselection()
        if not current or current[0] >= len(rows):
            return
        folder = output_dir / rows[current[0]]['id']
        if not folder.is_dir():
            messagebox.showinfo('尚无任务目录', '工作进程认领后会创建任务目录。', parent=root)
            return
        os.startfile(folder)

    refresh()
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--snapshot', action='store_true', help='Print a single JSON snapshot without a window')
    args = parser.parse_args()
    if args.snapshot:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
        try:
            print(json.dumps(snapshot(args.db, args.output_dir), ensure_ascii=False))
            return 0
        except (OSError, sqlite3.Error) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    show_window(args.db.resolve(), args.output_dir.resolve())
    return 0


if __name__ == '__main__':
    sys.exit(main())
