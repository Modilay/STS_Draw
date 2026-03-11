from __future__ import annotations

import threading
import time
from collections.abc import Callable
from ctypes import Structure, Union, byref, c_long, c_ulong, c_ulonglong, sizeof
from dataclasses import dataclass
from math import dist

from sts_draw.models import (
    DEFAULT_DRAW_SPEED_PROFILE as DEFAULT_SESSION_DRAW_SPEED_PROFILE,
    BezierStroke,
    ExecutionSession,
    LineStroke,
    MoveStroke,
)


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000
SM_CXSCREEN = 0
SM_CYSCREEN = 1
LEFT_BUTTON_FLAGS = (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
RIGHT_BUTTON_FLAGS = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
DEFAULT_DRAW_SPEED_PROFILE = DEFAULT_SESSION_DRAW_SPEED_PROFILE


class _MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", c_long),
        ("dy", c_long),
        ("mouseData", c_ulong),
        ("dwFlags", c_ulong),
        ("time", c_ulong),
        ("dwExtraInfo", c_ulonglong),
    ]


class _INPUT_UNION(Union):
    _fields_ = [("mouse_input", _MOUSEINPUT)]


class _INPUT(Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", c_ulong), ("data", _INPUT_UNION)]


@dataclass(slots=True)
class ExecutorSettings:
    drag_step_pixels: int = 8
    min_step_delay_ms: int = 10
    path_settle_ms: int = 25
    completion_settle_ms: int = 120
    move_step_pixels: int = 12


def executor_settings_for_profile(profile: str) -> ExecutorSettings:
    normalized = profile.lower() if isinstance(profile, str) else DEFAULT_DRAW_SPEED_PROFILE
    presets = {
        "stable": ExecutorSettings(
            drag_step_pixels=8,
            min_step_delay_ms=10,
            path_settle_ms=25,
            completion_settle_ms=120,
            move_step_pixels=12,
        ),
        "balanced": ExecutorSettings(
            drag_step_pixels=10,
            min_step_delay_ms=5,
            path_settle_ms=12,
            completion_settle_ms=45,
            move_step_pixels=16,
        ),
        "fast": ExecutorSettings(
            drag_step_pixels=12,
            min_step_delay_ms=2,
            path_settle_ms=6,
            completion_settle_ms=20,
            move_step_pixels=20,
        ),
    }
    return presets.get(normalized, presets[DEFAULT_DRAW_SPEED_PROFILE])


class DrawExecutor:
    def __init__(self, settings: ExecutorSettings | None = None) -> None:
        self.settings = settings or executor_settings_for_profile(DEFAULT_DRAW_SPEED_PROFILE)
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._pen_is_down = False
        self._current_user32 = None
        self._current_down_event: int | None = None
        self._current_up_event: int | None = None
        self._current_cursor: tuple[int, int] | None = None
        self._active_session: ExecutionSession | None = None
        self._status_callback: Callable[[str], None] | None = None

    def start(
        self,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        if session.stroke_plan is None:
            raise RuntimeError("No stroke plan is ready.")

        self._cancel_event.clear()
        self._pause_event.clear()
        self._resume_event.set()
        self._active_session = session
        self._status_callback = status_callback
        self._set_status(session, "countdown", status_callback)
        if self._sleep_with_cancel(0.01, session, status_callback):
            return
        self._set_status(session, "running", status_callback)

        try:
            import ctypes

            user32 = ctypes.windll.user32
        except Exception:
            user32 = None
        self._current_user32 = user32
        down_event, up_event = _mouse_button_flags(session.draw_mouse_button)
        self._current_down_event = down_event
        self._current_up_event = up_event
        self._pen_is_down = False
        self._current_cursor = None

        try:
            for index, segment in enumerate(session.stroke_plan.segments):
                if self._cancel_requested(session, status_callback):
                    return
                next_segment = session.stroke_plan.segments[index + 1] if index + 1 < len(session.stroke_plan.segments) else None
                if user32 is not None:
                    if isinstance(segment, MoveStroke):
                        self._ensure_pen_up(user32, up_event)
                        if self._move_cursor_to(
                            segment.point,
                            self.settings.move_step_pixels,
                            user32,
                            session,
                            status_callback,
                        ):
                            return
                        if self._sleep_with_cancel(self.settings.path_settle_ms / 1000, session, status_callback):
                            return
                    elif isinstance(segment, LineStroke):
                        keep_pen_down = isinstance(next_segment, (LineStroke, BezierStroke)) and next_segment.continues_path
                        if self._drag_segment(
                            _interpolate_drag_points(
                                segment.start,
                                segment.end,
                                step_pixels=self.settings.drag_step_pixels,
                            ),
                            down_event,
                            up_event,
                            segment.estimated_duration_ms / 1000,
                            user32,
                            session,
                            status_callback,
                            start_new_path=not segment.continues_path,
                            release_pen=not keep_pen_down,
                        ):
                            return
                    elif isinstance(segment, BezierStroke):
                        keep_pen_down = isinstance(next_segment, (LineStroke, BezierStroke)) and next_segment.continues_path
                        if self._drag_segment(
                            _sample_bezier_drag_points(
                                segment,
                                step_pixels=self.settings.drag_step_pixels,
                            ),
                            down_event,
                            up_event,
                            segment.estimated_duration_ms / 1000,
                            user32,
                            session,
                            status_callback,
                            start_new_path=not segment.continues_path,
                            release_pen=not keep_pen_down,
                        ):
                            return
                if self._cancel_requested(session, status_callback):
                    return

            if self._cancel_requested(session, status_callback):
                return
            if self._sleep_with_cancel(self.settings.completion_settle_ms / 1000, session, status_callback):
                return
            self._set_status(session, "completed", status_callback)
        finally:
            if user32 is not None:
                self._ensure_pen_up(user32, up_event)
            self._pen_is_down = False
            self._pause_event.clear()
            self._resume_event.set()
            self._current_user32 = None
            self._current_down_event = None
            self._current_up_event = None
            self._current_cursor = None
            self._active_session = None
            self._status_callback = None

    def cancel(self) -> None:
        self._cancel_event.set()
        self._pause_event.clear()
        self._resume_event.set()

    def pause(self) -> None:
        if self._active_session is None or self._active_session.status in {"cancelled", "completed"}:
            return
        self._pause_event.set()
        self._resume_event.clear()

    def resume(self) -> None:
        if self._active_session is None:
            return
        self._pause_event.clear()
        self._resume_event.set()

    def toggle_pause(self) -> None:
        if self._active_session is not None and self._active_session.status == "paused":
            self.resume()
            return
        self.pause()

    def _cancel_requested(
        self,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None,
    ) -> bool:
        if not self._cancel_event.is_set():
            return False
        if self._current_user32 is not None and self._current_up_event is not None:
            self._ensure_pen_up(self._current_user32, self._current_up_event)
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
        down_event: int | None = None,
        up_event: int | None = None,
        resume_status: str = "running",
    ) -> bool:
        remaining = max(seconds, 0.0)
        while remaining > 0:
            if self._wait_if_paused(
                session,
                status_callback,
                down_event=down_event,
                up_event=up_event,
                resume_status=resume_status,
            ):
                return True
            if self._cancel_requested(session, status_callback):
                return True
            chunk = min(0.005, remaining)
            start = time.perf_counter()
            time.sleep(chunk)
            remaining -= time.perf_counter() - start
        if self._wait_if_paused(
            session,
            status_callback,
            down_event=down_event,
            up_event=up_event,
            resume_status=resume_status,
        ):
            return True
        return self._cancel_requested(session, status_callback)

    def _drag_segment(
        self,
        points: list[tuple[int, int]],
        down_event: int,
        up_event: int,
        duration_seconds: float,
        user32,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None,
        start_new_path: bool,
        release_pen: bool,
    ) -> bool:
        if not points:
            return False
        self._move_cursor(user32, points[0])
        if self._wait_if_paused(session, status_callback, resume_status="running"):
            return True
        if start_new_path and not self._pen_is_down:
            _send_mouse_button(user32, down_event)
            self._pen_is_down = True
        segment_count = max(len(points) - 1, 1)
        step_delay = 0.0
        if len(points) > 1:
            step_delay = max(duration_seconds / segment_count, self.settings.min_step_delay_ms / 1000)

        try:
            for point in points[1:]:
                if self._wait_if_paused(
                    session,
                    status_callback,
                    down_event=down_event,
                    up_event=up_event,
                    resume_status="running",
                ):
                    return True
                if self._cancel_requested(session, status_callback):
                    return True
                self._move_cursor(user32, point)
                if step_delay > 0 and self._sleep_with_cancel(
                    step_delay,
                    session,
                    status_callback,
                    down_event=down_event,
                    up_event=up_event,
                    resume_status="running",
                ):
                    return True
        finally:
            if release_pen:
                self._ensure_pen_up(user32, up_event)
        return False

    def _ensure_pen_up(self, user32, up_event: int) -> None:
        if not self._pen_is_down:
            return
        _send_mouse_button(user32, up_event)
        self._pen_is_down = False

    def _move_cursor_to(
        self,
        target: tuple[int, int],
        step_pixels: int,
        user32,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None,
    ) -> bool:
        points = _interpolate_pen_up_points(self._current_cursor, target, step_pixels)
        for point in points:
            if self._wait_if_paused(session, status_callback, resume_status="running"):
                return True
            if self._cancel_requested(session, status_callback):
                return True
            self._move_cursor(user32, point)
        return False

    def _wait_if_paused(
        self,
        session: ExecutionSession,
        status_callback: Callable[[str], None] | None,
        down_event: int | None = None,
        up_event: int | None = None,
        resume_status: str = "running",
    ) -> bool:
        paused_here = False
        should_restore_pen = False
        while self._pause_event.is_set():
            if (
                not should_restore_pen
                and down_event is not None
                and up_event is not None
                and self._current_user32 is not None
                and self._pen_is_down
            ):
                self._ensure_pen_up(self._current_user32, up_event)
                should_restore_pen = True
            if session.status != "paused":
                self._set_status(session, "paused", status_callback)
            paused_here = True
            self._resume_event.wait(0.01)
            if self._cancel_requested(session, status_callback):
                return True
        if paused_here:
            if should_restore_pen and down_event is not None and self._current_user32 is not None and not self._pen_is_down:
                _send_mouse_button(self._current_user32, down_event)
                self._pen_is_down = True
            self._set_status(session, resume_status, status_callback)
        return False

    def _move_cursor(self, user32, point: tuple[int, int]) -> None:
        if self._current_cursor == point:
            return
        _send_cursor_move(user32, point[0], point[1])
        self._current_cursor = point


def _interpolate_drag_points(
    start: tuple[int, int],
    end: tuple[int, int],
    step_pixels: int = 4,
) -> list[tuple[int, int]]:
    total_distance = dist(start, end)
    if total_distance <= 0:
        return [start]

    step_count = max(int(total_distance / max(step_pixels, 1)), 1)
    points: list[tuple[int, int]] = [start]
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


def _sample_bezier_drag_points(stroke: BezierStroke, step_pixels: int = 4) -> list[tuple[int, int]]:
    samples = stroke.sample_points(steps=32)
    if not samples:
        return [stroke.start, stroke.end]
    points = [stroke.start]
    last = stroke.start
    for point in samples:
        if dist(last, point) >= step_pixels:
            points.append(point)
            last = point
    if points[-1] != stroke.end:
        points.append(stroke.end)
    return points


def _interpolate_pen_up_points(
    start: tuple[int, int] | None,
    end: tuple[int, int],
    step_pixels: int = 12,
) -> list[tuple[int, int]]:
    if start is None:
        return [end]
    points = _interpolate_drag_points(start, end, step_pixels=max(step_pixels, 1))
    return [point for point in points[1:] if point != start]


def _mouse_button_flags(button: str) -> tuple[int, int]:
    if button == "right":
        return RIGHT_BUTTON_FLAGS
    return LEFT_BUTTON_FLAGS


def _send_cursor_move(user32, x: int, y: int) -> None:
    if hasattr(user32, "SendInput") and hasattr(user32, "GetSystemMetrics"):
        dx, dy = _normalize_absolute_coordinates(user32, x, y)
        _send_input(user32, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, dx=dx, dy=dy)
        return
    user32.SetCursorPos(x, y)


def _send_mouse_button(user32, event_flag: int) -> None:
    if hasattr(user32, "SendInput"):
        _send_input(user32, event_flag)
        return
    user32.mouse_event(event_flag, 0, 0, 0, 0)


def _normalize_absolute_coordinates(user32, x: int, y: int) -> tuple[int, int]:
    screen_width = max(int(user32.GetSystemMetrics(SM_CXSCREEN)), 1)
    screen_height = max(int(user32.GetSystemMetrics(SM_CYSCREEN)), 1)
    normalized_x = round(x * 65535 / max(screen_width - 1, 1))
    normalized_y = round(y * 65535 / max(screen_height - 1, 1))
    return normalized_x, normalized_y


def _send_input(user32, flags: int, dx: int = 0, dy: int = 0) -> None:
    input_record = _INPUT(
        type=INPUT_MOUSE,
        data=_INPUT_UNION(
            mouse_input=_MOUSEINPUT(
                dx=dx,
                dy=dy,
                mouseData=0,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )
    user32.SendInput(1, byref(input_record), sizeof(_INPUT))
