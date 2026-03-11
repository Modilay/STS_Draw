import threading
import unittest
from unittest.mock import patch

from sts_draw.draw_executor import DrawExecutor
from sts_draw.models import CalibrationRegion, ExecutionSession, StrokePlan, StrokeSegment


class FakeUser32:
    def __init__(self) -> None:
        self.positions: list[tuple[int, int]] = []
        self.events: list[int] = []

    def SetCursorPos(self, x: int, y: int) -> None:
        self.positions.append((x, y))

    def mouse_event(self, event_flag: int, *_args) -> None:
        self.events.append(event_flag)
        return None


class DrawExecutorTests(unittest.TestCase):
    def test_pen_down_segment_drags_from_start_to_end_in_multiple_steps(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    StrokeSegment(start=(10, 20), end=(30, 40), pen_down=True, speed_pixels_per_second=100),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            executor.start(session)

        self.assertEqual(session.status, "completed")
        self.assertEqual(fake_user32.positions[0], (10, 20))
        self.assertEqual(fake_user32.positions[-1], (30, 40))
        self.assertGreater(len(fake_user32.positions), 2)
        self.assertEqual(fake_user32.events, [0x0002, 0x0004])

    def test_cancel_stops_before_processing_all_segments(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    StrokeSegment(start=(0, 0), end=(100, 100), pen_down=True, speed_pixels_per_second=1)
                    for _ in range(5)
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()
        thread = threading.Thread(target=executor.start, args=(session,))

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            thread.start()
            while session.status not in {"running", "cancelled", "completed"}:
                pass
            executor.cancel()
            thread.join(timeout=2)

        self.assertEqual(session.status, "cancelled")
        self.assertLess(len(fake_user32.positions), len(session.stroke_plan.segments))


if __name__ == "__main__":
    unittest.main()
