from __future__ import annotations

import threading
import time

from sts_draw.models import ExecutionSession


LEFT_BUTTON_FLAGS = (0x0002, 0x0004)
RIGHT_BUTTON_FLAGS = (0x0008, 0x0010)


class DrawExecutor:
    def __init__(self) -> None:
        self._cancel_event = threading.Event()

    def start(self, session: ExecutionSession) -> None:
        if session.stroke_plan is None:
            raise RuntimeError("No stroke plan is ready.")

        self._cancel_event.clear()
        session.status = "countdown"
        time.sleep(0.01)
        session.status = "running"

        try:
            import ctypes

            user32 = ctypes.windll.user32
        except Exception:
            user32 = None
        down_event, up_event = _mouse_button_flags(session.draw_mouse_button)

        for segment in session.stroke_plan.segments:
            if self._cancel_event.is_set():
                session.cancel("hotkey")
                return
            if user32 is not None:
                user32.SetCursorPos(segment.end[0], segment.end[1])
                if segment.pen_down:
                    user32.mouse_event(down_event, 0, 0, 0, 0)
                    user32.mouse_event(up_event, 0, 0, 0, 0)
            time.sleep(min(segment.estimated_duration_ms / 1000, 0.02))

        session.status = "completed"

    def cancel(self) -> None:
        self._cancel_event.set()


def _mouse_button_flags(button: str) -> tuple[int, int]:
    if button == "right":
        return RIGHT_BUTTON_FLAGS
    return LEFT_BUTTON_FLAGS
