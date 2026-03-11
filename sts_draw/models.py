from __future__ import annotations

from dataclasses import dataclass, field
from math import dist

DEFAULT_HOTKEYS = {
    "calibrate": "ctrl+alt+c",
    "start": "ctrl+alt+d",
    "stop": "ctrl+alt+s",
}
DEFAULT_DRAW_MOUSE_BUTTON = "left"


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
class StrokeSegment:
    start: tuple[int, int]
    end: tuple[int, int]
    pen_down: bool
    speed_pixels_per_second: int = 250

    @property
    def estimated_duration_ms(self) -> int:
        pixels = dist(self.start, self.end)
        if self.speed_pixels_per_second <= 0:
            return 0
        return round((pixels / self.speed_pixels_per_second) * 1000)


@dataclass(slots=True)
class StrokePlan:
    segments: list[StrokeSegment]
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
    preview_scale: float | None = None

    def cancel(self, reason: str) -> None:
        self.status = "cancelled"
        self.error_reason = reason
