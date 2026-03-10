from __future__ import annotations

from dataclasses import dataclass

from sts_draw.models import CalibrationRegion, LineArtResult, StrokePlan


@dataclass(slots=True)
class PreviewPayload:
    line_art_size: tuple[int, int]
    region_bounds: tuple[int, int, int, int]
    segment_count: int


class PreviewRenderer:
    def render(
        self,
        line_art: LineArtResult,
        stroke_plan: StrokePlan,
        region: CalibrationRegion,
    ) -> PreviewPayload:
        return PreviewPayload(
            line_art_size=line_art.size,
            region_bounds=region.bounds,
            segment_count=len(stroke_plan.segments),
        )
