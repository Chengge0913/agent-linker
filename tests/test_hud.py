import sys
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from hud import transitions, read_events, flight_pose, route_pose, stage_progress, cancel_callbacks, acquire_hud_lock


class HudTests(unittest.TestCase):
    def test_only_one_hud_can_hold_queue_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / 'hud.lock'
            first = acquire_hud_lock(lock_path)
            self.assertIsNotNone(first)
            self.assertIsNone(acquire_hud_lock(lock_path))
            first.close()
            again = acquire_hud_lock(lock_path)
            self.assertIsNotNone(again)
            again.close()

    def test_close_cancels_pending_callbacks(self):
        class Root:
            def __init__(self): self.cancelled = []
            def after_cancel(self, identifier): self.cancelled.append(identifier)
        root = Root()
        pending = {'render': 'a', 'refresh': 'b'}
        cancel_callbacks(root, pending)
        self.assertEqual(set(root.cancelled), {'a', 'b'})
        self.assertEqual(pending, {})

    def test_stage_progress_uses_observed_stages_and_keeps_blocked_unknown(self):
        self.assertEqual(stage_progress({'state': 'pending'}), 0)
        self.assertEqual(stage_progress({'state': 'running', 'progress': {'stage': 'init'}}), 25)
        self.assertEqual(stage_progress({'state': 'running', 'progress': {'stage': 'tool'}}), 50)
        self.assertIsNone(stage_progress({'state': 'delivered'}))
        self.assertIsNone(stage_progress({'state': 'blocked'}))

    def test_route_is_smooth_and_inside_all_edge_layouts(self):
        layouts = [((27, 29), (27, 131), (1, 0)),
                   ((163, 29), (163, 131), (-1, 0)),
                   ((29, 27), (131, 27), (0, 1)),
                   ((29, 163), (131, 163), (0, -1))]
        for start, end, inward in layouts:
            for origin, destination in ((start, end), (end, start)):
                with self.subTest(origin=origin, destination=destination, inward=inward):
                    expected_start = tuple(origin[i] + 25 * inward[i] for i in range(2))
                    expected_end = tuple(destination[i] + 25 * inward[i] for i in range(2))
                    self.assertEqual(route_pose(0, origin, destination, inward)[1], expected_start)
                    self.assertEqual(route_pose(2.5, origin, destination, inward)[1], expected_end)
                    before = route_pose(0.449, origin, destination, inward)[1]
                    after = route_pose(0.451, origin, destination, inward)[1]
                    self.assertLess(sum((a-b)**2 for a,b in zip(before, after)), 0.1)
                    for step in range(101):
                        phase, point, heading = route_pose(0.45 + 1.999 * step / 100, origin, destination, inward)
                        self.assertTrue(all(8 <= coordinate <= 182 for coordinate in point))
                        self.assertAlmostEqual(sum(component**2 for component in heading), 1, places=5)

    def test_flight_follows_half_circle_in_each_orientation(self):
        layouts = [((27, 29), (27, 131), (1, 0)), ((163, 29), (163, 131), (-1, 0)),
                   ((29, 27), (131, 27), (0, 1)), ((29, 163), (131, 163), (0, -1))]
        for start, end, inward in layouts:
            for origin, destination in ((start, end), (end, start)):
                midpoint = tuple((origin[i] + destination[i]) / 2 + 25 * inward[i] for i in range(2))
                radius = ((origin[0] - destination[0])**2 + (origin[1] - destination[1])**2)**0.5 / 2
                for step in range(1, 20):
                    point = route_pose(0.45 + 2 * step / 20, origin, destination, inward)[1]
                    distance = ((point[0]-midpoint[0])**2 + (point[1]-midpoint[1])**2)**0.5
                    self.assertAlmostEqual(distance, radius, places=6)
                    self.assertGreaterEqual(sum((point[i]-midpoint[i])*inward[i] for i in range(2)), 0)

    def test_route_moves_from_sender_to_recipient_and_back(self):
        sender, recipient, inward = (27, 29), (27, 131), (1, 0)
        self.assertEqual(route_pose(0, sender, recipient, inward)[0:2], ('pack', (52, 29)))
        phase, middle, direction = route_pose(1.45, sender, recipient, inward)
        self.assertEqual(phase, 'flight')
        self.assertGreater(middle[0], sender[0])
        self.assertEqual(route_pose(2.5, sender, recipient, inward)[0:2], ('arrive', (52, 131)))
        self.assertEqual(route_pose(0, recipient, sender, inward)[0:2], ('pack', (52, 131)))
        self.assertEqual(route_pose(2.5, recipient, sender, inward)[0:2], ('arrive', (52, 29)))

    def test_flights_move_in_opposite_directions_and_finish_by_time(self):
        start = flight_pose(0, 'dispatch')
        middle = flight_pose(1.05, 'dispatch')
        end = flight_pose(2.1, 'dispatch')
        self.assertLess(start[1], middle[1])
        self.assertLess(middle[1], end[1])
        self.assertLess(middle[2], 0)
        self.assertEqual(flight_pose(0, 'return')[1], end[1])
        self.assertEqual(flight_pose(2.1, 'return')[1], start[1])
        self.assertEqual(flight_pose(20, 'return')[0], 1)

    def test_events_survive_fast_dispatch_and_return_between_polls(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            db = base / 'queue.sqlite'
            brief = base / 'brief.md'
            brief.write_text('test', encoding='utf-8')
            queue = Path(__file__).resolve().parent.parent / 'src' / 'queue.py'
            def run(*args):
                return json.loads(subprocess.check_output([sys.executable, str(queue), '--db', str(db), *args], text=True))
            run('status')
            cursor, initial = read_events(db)
            self.assertEqual(initial, [])
            task = run('publish', 'dsh', str(brief))
            run('claim', 'dsh')
            run('finish', 'dsh', task['id'], 'done')
            cursor, events = read_events(db, cursor)
            self.assertEqual(events, [('dsh', 'dispatch'), ('dsh', 'return')])
            self.assertEqual(read_events(db, cursor)[1], [])

    def test_transitions_only_show_new_dispatch_and_results(self):
        before = {'b': 'running'}
        rows = [{'id': 'a', 'worker': 'dsh', 'state': 'pending'},
                {'id': 'b', 'worker': 'wb', 'state': 'delivered'},
                {'id': 'c', 'worker': 'wb', 'state': 'pending'}]
        events = transitions(before, rows)
        self.assertEqual(events, [('dsh', 'dispatch'), ('wb', 'return'), ('wb', 'dispatch')])
