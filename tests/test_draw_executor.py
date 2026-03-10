import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sts_draw.draw_executor import DrawExecutor
from sts_draw.models import CalibrationRegion, ExecutionSession, StrokePlan, StrokeSegment


class FakeUser32:
    def __init__(self) -> None:
        self.flags: list[int] = []
        self.positions: list[tuple[int, int]] = []

    def SetCursorPos(self, x: int, y: int) -> None:
        self.positions.append((x, y))

    def mouse_event(self, flag: int, *_args) -> None:
        self.flags.append(flag)


class DrawExecutorTests(unittest.TestCase):
    def _session(self, mouse_button: str) -> ExecutionSession:
        session = ExecutionSession(draw_mouse_button=mouse_button)
        session.stroke_plan = StrokePlan(
            segments=[StrokeSegment(start=(0, 0), end=(10, 10), pen_down=True)],
            source_size=(10, 10),
            region=CalibrationRegion(left=0, top=0, width=10, height=10),
        )
        return session

    def test_uses_left_button_by_default(self) -> None:
        fake_user32 = FakeUser32()
        session = self._session("left")

        with patch("ctypes.windll", SimpleNamespace(user32=fake_user32), create=True), patch(
            "time.sleep", return_value=None
        ):
            DrawExecutor().start(session)

        self.assertEqual(fake_user32.flags, [0x0002, 0x0004])

    def test_uses_right_button_when_configured(self) -> None:
        fake_user32 = FakeUser32()
        session = self._session("right")

        with patch("ctypes.windll", SimpleNamespace(user32=fake_user32), create=True), patch(
            "time.sleep", return_value=None
        ):
            DrawExecutor().start(session)

        self.assertEqual(fake_user32.flags, [0x0008, 0x0010])


if __name__ == "__main__":
    unittest.main()
