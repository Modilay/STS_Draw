from __future__ import annotations

from dataclasses import dataclass, field
from math import dist
from typing import TypeAlias

DEFAULT_HOTKEYS = {
    "calibrate": "ctrl+alt+c",
    "start": "ctrl+alt+d",
    "stop": "ctrl+alt+s",
    "pause": "ctrl+alt+p",
}
DEFAULT_DRAW_MOUSE_BUTTON = "left"
DEFAULT_DRAW_SPEED_PROFILE = "balanced"


@dataclass(slots=True)
class LineArtResult:
    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    prompt: str | None = None

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


@dataclass(slots=True)
class CalibrationRegion:
    left: int
    top: int
    width: int
    height: int

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.width, self.height

    def map_point(self, point: tuple[float, float]) -> tuple[int, int]:
        x_ratio, y_ratio = point
        return (
            self.left + round(self.width * x_ratio),
            self.top + round(self.height * y_ratio),
        )


@dataclass(slots=True)
class PreviewPlacementResult:
    region: CalibrationRegion
    scale: float


@dataclass(slots=True)
class MoveStroke:
    point: tuple[int, int]

    @property
    def estimated_duration_ms(self) -> int:
        return 0


@dataclass(slots=True)
class LineStroke:
    start: tuple[int, int]
    end: tuple[int, int]
    speed_pixels_per_second: int = 250
    continues_path: bool = False

    @property
    def estimated_duration_ms(self) -> int:
        pixels = dist(self.start, self.end)
        if self.speed_pixels_per_second <= 0:
            return 0
        return round((pixels / self.speed_pixels_per_second) * 1000)


@dataclass(slots=True)
class BezierStroke:
    start: tuple[int, int]
    control1: tuple[int, int]
    control2: tuple[int, int]
    end: tuple[int, int]
    speed_pixels_per_second: int = 250
    continues_path: bool = False

    @property
    def estimated_duration_ms(self) -> int:
        pixels = 0.0
        previous = self.start
        for point in self.sample_points(steps=16):
            pixels += dist(previous, point)
            previous = point
        if self.speed_pixels_per_second <= 0:
            return 0
        return round((pixels / self.speed_pixels_per_second) * 1000)

    def sample_points(self, steps: int = 16) -> list[tuple[int, int]]:
        count = max(steps, 1)
        points: list[tuple[int, int]] = []
        for index in range(1, count + 1):
            t = index / count
            mt = 1.0 - t
            x = (
                (mt**3) * self.start[0]
                + 3 * (mt**2) * t * self.control1[0]
                + 3 * mt * (t**2) * self.control2[0]
                + (t**3) * self.end[0]
            )
            y = (
                (mt**3) * self.start[1]
                + 3 * (mt**2) * t * self.control1[1]
                + 3 * mt * (t**2) * self.control2[1]
                + (t**3) * self.end[1]
            )
            point = (round(x), round(y))
            if not points or point != points[-1]:
                points.append(point)
        if not points or points[-1] != self.end:
            points.append(self.end)
        return points


Stroke: TypeAlias = MoveStroke | LineStroke | BezierStroke


@dataclass(slots=True)
class StrokePlan:
    segments: list[Stroke]
    source_size: tuple[int, int]
    region: CalibrationRegion


@dataclass(slots=True)
class HotkeyStatus:
    hotkey: str
    is_active: bool = True
    conflict_reason: str | None = None
    message: str = "active"


@dataclass(slots=True)
class ExecutionSession:
    status: str = "idle"
    error_reason: str | None = None
    last_preview: object | None = None
    active_region: CalibrationRegion | None = None
    stroke_plan: StrokePlan | None = None
    line_art: LineArtResult | None = None
    image_path: str | None = None
    hotkeys: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    hotkey_statuses: dict[str, HotkeyStatus] = field(
        default_factory=lambda: {
            action: HotkeyStatus(hotkey=value)
            for action, value in DEFAULT_HOTKEYS.items()
        }
    )
    draw_mouse_button: str = DEFAULT_DRAW_MOUSE_BUTTON
    draw_speed_profile: str = DEFAULT_DRAW_SPEED_PROFILE
    preview_scale: float | None = None

    def cancel(self, reason: str) -> None:
        self.status = "cancelled"
        self.error_reason = reason
