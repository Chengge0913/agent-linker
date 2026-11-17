"""Small local window showing queue activity for Codex, DSH and WB."""
import argparse
import ctypes
import hashlib
import os
import math
import sqlite3
import time
import tkinter as tk
from contextlib import closing
from pathlib import Path

from progress import snapshot


def stage_progress(task):
    """Coarse observed lifecycle stages, not a measurement of work completed."""
    if not task or task['state'] in ('blocked', 'delivered'):
        return None
    if task['state'] == 'pending':
        return 0
    stage = (task.get('progress') or {}).get('stage')
    return {'claimed': 10, 'init': 25, 'tool': 50, 'result_received': 90}.get(stage, 10)


def flight_pose(elapsed, kind):
    """Return distance, curve offset and heading along the flight path."""
    t = min(max(elapsed / 2.1, 0.0), 1.0)
    eased = t * t * (3.0 - 2.0 * t)
    direction = 1 if kind == 'dispatch' else -1
    travel = 24 + 95 * (eased if direction == 1 else 1 - eased)
    curve = -30 * math.sin(math.pi * eased)
    heading = math.atan2(-30 * math.pi * math.cos(math.pi * eased), 95 * direction)
    return t, travel, curve, heading


def route_pose(elapsed, origin, destination, inward):
    """Return a continuous position and tangent for all screen edges."""
    start = tuple(origin[i] + 25 * inward[i] for i in range(2))
    end = tuple(destination[i] + 25 * inward[i] for i in range(2))
    if elapsed < 0.45:
        return 'pack', start, inward
    if elapsed >= 2.75:
        return 'done', end, inward
    if elapsed >= 2.45:
        return 'arrive', end, inward
    t = max(0.0, min(1.0, (elapsed - 0.45) / 2.0))
    u = t * t * t * (t * (t * 6 - 15) + 10)
    radius = math.hypot(end[0] - start[0], end[1] - start[1]) / 2
    angle = math.pi * u
    along = (1 - math.cos(angle)) / 2
    bulge = radius * math.sin(angle)
    x = start[0] * (1-along) + end[0] * along + inward[0] * bulge
    y = start[1] * (1-along) + end[1] * along + inward[1] * bulge
    dx = (end[0] - start[0]) * math.sin(angle) / 2 + inward[0] * radius * math.cos(angle)
    dy = (end[1] - start[1]) * math.sin(angle) / 2 + inward[1] * radius * math.cos(angle)
    length = math.hypot(dx, dy)
    direction = (dx/length, dy/length) if length else inward
    return 'flight', (x, y), direction


def read_events(db, after=None):
    if not db.is_file():
        return (0 if after is None else after), []
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as connection:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_events'").fetchone()
        if not exists:
            return (0 if after is None else after), []
        if after is None:
            return connection.execute('SELECT COALESCE(MAX(id), 0) FROM task_events').fetchone()[0], []
        events = connection.execute('SELECT id, worker, kind FROM task_events WHERE id > ? ORDER BY id', (after,)).fetchall()
        return (events[-1][0] if events else after), [(worker, kind) for _, worker, kind in events]


def transitions(previous, rows):
    events = []
    for row in rows:
        old = previous.get(row['id'])
        if old is None and row['state'] == 'pending':
            events.append((row['worker'], 'dispatch'))
        elif old == 'running' and row['state'] in ('delivered', 'blocked'):
            events.append((row['worker'], 'return'))
    return events


def acquire_hud_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    if stream.seek(0, 2) == 0:
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
        return None
    return stream


def cancel_callbacks(root, pending):
    for identifier in list(pending.values()):
        root.after_cancel(identifier)
    pending.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    key = hashlib.sha256(f'{args.db.resolve()}\0{args.output_dir.resolve()}'.encode('utf-8')).hexdigest()[:16]
    hud_lock = acquire_hud_lock(Path.home() / '.agent-linker' / 'dashboards' / f'{key}-hud.lock')
    if hud_lock is None:
        return
    if hasattr(ctypes, 'windll'):
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    root.title('Agent Linker')
    # Keep enough transparent space on the inward side for the paper plane.
    root.overrideredirect(True)
    root.wm_attributes('-transparentcolor', '#010203')
    root.attributes('-topmost', True)
    root.configure(bg='#010203')
    canvas = tk.Canvas(root, bg='#010203', highlightthickness=0)
    canvas.pack(fill='both', expand=True)
    assets = Path(__file__).resolve().parent / 'assets'
    icons = {
        'codex': tk.PhotoImage(file=str(assets / 'openai-hud.png')),
        'dsh': tk.PhotoImage(file=str(assets / 'deepseek-color-hud.png')),
        'wb': tk.PhotoImage(file=str(assets / 'codebuddy-color-hud.png')),
    }
    rings = {value: tk.PhotoImage(file=str(assets / f'ring-{value}.png'))
             for value in (0, 10, 25, 50, 90, 100)}
    agents = ('codex', 'dsh', 'wb')
    edge = 'left'
    previous = None
    event_cursor = None
    rows = []
    flights = []
    hovering = False
    drag = None
    pending = {}
    closing = False
    last_frame = None

    def layout():
        # Bar centers and inward vector, in window coordinates.
        if edge == 'left': return [(27, 29+i*51) for i in range(3)], (1, 0)
        if edge == 'right': return [(163, 29+i*51) for i in range(3)], (-1, 0)
        if edge == 'top': return [(29+i*51, 27) for i in range(3)], (0, 1)
        return [(29+i*51, 163) for i in range(3)], (0, -1)

    def snap(screen_x, screen_y):
        nonlocal edge
        width, height = root.winfo_screenwidth(), root.winfo_screenheight()
        distances = {'left': screen_x, 'right': width-screen_x,
                     'top': screen_y, 'bottom': height-screen_y}
        edge = min(distances, key=distances.get)
        if edge in ('left', 'right'):
            x = 0 if edge == 'left' else max(0, width-190)
            y = min(max(0, screen_y-80), max(0, height-160))
            root.geometry(f'190x160+{x}+{y}')
        else:
            x = min(max(0, screen_x-80), max(0, width-160))
            y = 0 if edge == 'top' else max(0, height-190)
            root.geometry(f'160x190+{x}+{y}')

    def render():
        nonlocal last_frame
        stages = tuple(
            stage_progress(next((row for row in rows
                                 if row['worker'] == agent and row['state'] in ('running', 'pending')), None))
            for agent in agents
        )
        frame = (edge, hovering, stages)
        pending['render'] = root.after(16, render)
        if frame == last_frame and not flights:
            return
        # Redraw once after the final flight frame to clear its trail.
        last_frame = None if flights else frame
        canvas.delete('all')
        points, inward = layout()
        first, last = points[0], points[-1]
        canvas.create_line(*first, *last, fill='#08090c', width=49, capstyle=tk.ROUND)
        for agent, (x, y), value in zip(agents, points, stages):
            canvas.create_image(x, y, image=rings[value if value is not None else 0])
            canvas.create_image(x, y, image=icons[agent])
        if hovering:
            x, y = last
            canvas.create_text(x+inward[0]*29, y+inward[1]*29,
                               text='×', fill='#d4d4dc', font=('Arial', 11, 'bold'), tags='close')
        for flight in flights[:]:
            target = points[agents.index(flight['worker'])]
            sender = points[0]
            origin, destination = (sender, target) if flight['kind'] == 'dispatch' else (target, sender)
            phase, (px, py), (dx, dy) = route_pose(time.monotonic() - flight['started'], origin, destination, inward)
            if phase == 'done':
                flights.remove(flight)
                continue
            path=[]
            for step in range(25):
                _, (path_x, path_y), _ = route_pose(0.45+1.999*step/24, origin, destination, inward)
                path.extend((path_x, path_y))
            if phase == 'flight':
                canvas.create_line(*path, smooth=True, dash=(4, 6), fill='#666b7a', width=1)
            fill = '#f3f5f7' if flight['kind'] != 'blocked' else '#f2a26f'
            if phase in ('pack', 'arrive'):
                ex, ey = px, py
                canvas.create_rectangle(ex-9, ey-6, ex+9, ey+6, fill=fill, outline='#353945')
                canvas.create_line(ex-9, ey-6, ex, ey+1, ex+9, ey-6, fill='#757b87')
            else:
                canvas.create_polygon(px+dx*13, py+dy*13, px-dx*9-dy*7, py-dy*9+dx*7,
                                      px-dx*5, py-dy*5, px-dx*9+dy*7, py-dy*9-dx*7,
                                      fill=fill, outline='#353945', width=1)
                canvas.create_line(px+dx*13, py+dy*13, px-dx*5, py-dy*5, fill='#757b87', width=1)

    def refresh():
        nonlocal previous, rows, event_cursor
        try:
            rows = snapshot(args.db.resolve(), args.output_dir.resolve())
            cursor, events = read_events(args.db.resolve(), event_cursor)
            if cursor is None and previous is not None:
                events = transitions(previous, rows)
            event_cursor = cursor
            for worker, kind in events:
                if worker in agents:
                    flights.append({'worker': worker, 'kind': kind, 'started': time.monotonic()})
            previous = {row['id']: row['state'] for row in rows}
        except (OSError, sqlite3.Error):
            pass
        pending['refresh'] = root.after(1000, refresh)

    def motion(event):
        nonlocal hovering
        points, _ = layout()
        hovering = any((event.x-x)**2+(event.y-y)**2 <= 36**2 for x, y in points)

    def press(event):
        nonlocal drag
        drag = (event.x_root-root.winfo_x(), event.y_root-root.winfo_y())

    def move(event):
        if drag:
            root.geometry(f'+{max(0,event.x_root-drag[0])}+{max(0,event.y_root-drag[1])}')

    def release(event):
        nonlocal drag
        if drag:
            snap(event.x_root, event.y_root)
            drag = None

    def close(event=None):
        nonlocal closing
        if not closing:
            closing = True
            cancel_callbacks(root, pending)
            root.destroy()
        return 'break'

    root.protocol('WM_DELETE_WINDOW', close)
    canvas.tag_bind('close', '<Button-1>', close)
    canvas.bind('<Motion>', motion)
    canvas.bind('<Leave>', lambda event: motion_out())
    def motion_out():
        nonlocal hovering
        hovering = False
    canvas.bind('<ButtonPress-1>', press)
    canvas.bind('<B1-Motion>', move)
    canvas.bind('<ButtonRelease-1>', release)
    snap(root.winfo_screenwidth()//2, 0)
    refresh()
    render()
    try:
        root.mainloop()
    finally:
        hud_lock.close()


if __name__ == '__main__':
    main()
