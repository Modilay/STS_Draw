from __future__ import annotations

from dataclasses import dataclass

from sts_draw.models import CalibrationRegion, StrokePlan, StrokeSegment


@dataclass(slots=True)
class PlannerSettings:
    speed_pixels_per_second: int = 250


class StrokePlanner:
    def __init__(self, settings: PlannerSettings | None = None) -> None:
        self.settings = settings or PlannerSettings()

    def plan(self, matrix: list[list[int]], region: CalibrationRegion) -> StrokePlan:
        if not matrix or not matrix[0]:
            raise ValueError("Line art matrix must not be empty.")

        rows = len(matrix)
        cols = len(matrix[0])
        x_step = region.width / cols
        y_step = region.height / rows
        segments: list[StrokeSegment] = []

        for row_index, row in enumerate(matrix):
            run_start: int | None = None
            for col_index, cell in enumerate(row + [0]):
                if cell and run_start is None:
                    run_start = col_index
                    continue
                if run_start is None or cell:
                    continue

                start = self._to_screen_point(run_start, row_index, x_step, y_step, region)
                end = self._to_screen_point(col_index - 1, row_index, x_step, y_step, region)
                segments.append(
                    StrokeSegment(start=start, end=start, pen_down=False, speed_pixels_per_second=self.settings.speed_pixels_per_second)
                )
                if start != end:
                    segments.append(
                        StrokeSegment(start=start, end=end, pen_down=True, speed_pixels_per_second=self.settings.speed_pixels_per_second)
                    )
                run_start = None

        return StrokePlan(segments=segments, source_size=(cols, rows), region=region)

    @staticmethod
    def _to_screen_point(
        col_index: int,
        row_index: int,
        x_step: float,
        y_step: float,
        region: CalibrationRegion,
    ) -> tuple[int, int]:
        x = region.left + round(col_index * x_step)
        y = region.top + round(row_index * y_step)
        return x, y
