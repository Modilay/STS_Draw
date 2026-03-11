from __future__ import annotations

import threading
import time
from collections.abc import Callable
from math import dist

from sts_draw.models import ExecutionSession


LEFT_BUTTON_FLAGS = (0x0002, 0x0004)
RIGHT_BUTTON_FLAGS = (0x0008, 0x0010)


class DrawExecutor:
    def __init__(self) -> None:
        self._cancel_event = threading.Event()

    def start(
        self,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        if session.stroke_plan is None:
            raise RuntimeError("No stroke plan is ready.")

        self._cancel_event.clear()
        self._set_status(session, "countdown", status_callback)
        if self._sleep_with_cancel(0.01, session, status_callback):
            return
        self._set_status(session, "running", status_callback)

        try:
            import ctypes

            user32 = ctypes.windll.user32
        except Exception:
            user32 = None
        down_event, up_event = _mouse_button_flags(session.draw_mouse_button)

        for segment in session.stroke_plan.segments:
            if self._cancel_requested(session, status_callback):
                return
            if user32 is not None:
                if segment.pen_down:
                    if self._drag_segment(
                        segment.start,
                        segment.end,
                        down_event,
                        up_event,
                        segment.estimated_duration_ms / 1000,
                        user32,
                        session,
                        status_callback,
                    ):
                        return
                else:
                    user32.SetCursorPos(segment.end[0], segment.end[1])
            if self._cancel_requested(session, status_callback):
                return
            if not segment.pen_down and self._sleep_with_cancel(
                min(segment.estimated_duration_ms / 1000, 0.02),
                session,
                status_callback,
            ):
                return

        if self._cancel_requested(session, status_callback):
            return
        self._set_status(session, "completed", status_callback)

    def cancel(self) -> None:
        self._cancel_event.set()

    def _cancel_requested(
        self,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None,
    ) -> bool:
        if not self._cancel_event.is_set():
            return False
        session.cancel("hotkey")
        if status_callback is not None:
            status_callback("cancelled")
        return True

    def _set_status(
        self,
        session: ExecutionSession,
        status: str,
        status_callback: Callable[[str], None] | None,
    ) -> None:
        session.status = status
        if status_callback is not None:
            status_callback(status)

    def _sleep_with_cancel(
        self,
        seconds: float,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None,
    ) -> bool:
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            if self._cancel_requested(session, status_callback):
                return True
            time.sleep(min(0.005, max(deadline - time.perf_counter(), 0)))
        return self._cancel_requested(session, status_callback)

    def _drag_segment(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        down_event: int,
        up_event: int,
        duration_seconds: float,
        user32,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None,
    ) -> bool:
        user32.SetCursorPos(start[0], start[1])
        user32.mouse_event(down_event, 0, 0, 0, 0)
        points = _interpolate_drag_points(start, end)
        step_delay = duration_seconds / len(points) if points else 0.0

        try:
            for point in points:
                if self._cancel_requested(session, status_callback):
                    return True
                user32.SetCursorPos(point[0], point[1])
                if step_delay > 0 and self._sleep_with_cancel(step_delay, session, status_callback):
                    return True
        finally:
            user32.mouse_event(up_event, 0, 0, 0, 0)
        return False


def _interpolate_drag_points(
    start: tuple[int, int],
    end: tuple[int, int],
    step_pixels: int = 4,
) -> list[tuple[int, int]]:
    total_distance = dist(start, end)
    if total_distance <= 0:
        return [end]

    step_count = max(int(total_distance / max(step_pixels, 1)), 1)
    points: list[tuple[int, int]] = []
    for index in range(1, step_count + 1):
        progress = index / step_count
        point = (
            round(start[0] + (end[0] - start[0]) * progress),
            round(start[1] + (end[1] - start[1]) * progress),
        )
        if not points or point != points[-1]:
            points.append(point)
    if points[-1] != end:
        points.append(end)
    return points


def _mouse_button_flags(button: str) -> tuple[int, int]:
    if button == "right":
        return RIGHT_BUTTON_FLAGS
    return LEFT_BUTTON_FLAGS
