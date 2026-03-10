from __future__ import annotations

import threading
import time

from sts_draw.models import ExecutionSession


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

        for segment in session.stroke_plan.segments:
            if self._cancel_event.is_set():
                session.cancel("hotkey")
                return
            if user32 is not None:
                user32.SetCursorPos(segment.end[0], segment.end[1])
                if segment.pen_down:
                    user32.mouse_event(0x0002, 0, 0, 0, 0)
                    user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(min(segment.estimated_duration_ms / 1000, 0.02))

        session.status = "completed"

    def cancel(self) -> None:
        self._cancel_event.set()
