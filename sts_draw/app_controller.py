from __future__ import annotations

from dataclasses import dataclass

from sts_draw.draw_executor import DrawExecutor
from sts_draw.image_generation_client import OpenAICompatibleClient
from sts_draw.models import CalibrationRegion, ExecutionSession, StrokePlan
from sts_draw.preview_renderer import PreviewRenderer
from sts_draw.stroke_planner import StrokePlanner


@dataclass(slots=True)
class LineArtMatrixFactory:
    def from_line_art(self, line_art) -> list[list[int]]:
        try:
            from PySide6 import QtCore, QtGui
        except ImportError:
            width = max(line_art.width, 1)
            height = max(line_art.height, 1)
            return [[1 if (x + y) % 2 == 0 else 0 for x in range(width)] for y in range(height)]

        image = QtGui.QImage()
        byte_array = QtCore.QByteArray(line_art.image_bytes)
        if not image.loadFromData(byte_array):
            raise RuntimeError("Unable to decode line art image.")

        matrix: list[list[int]] = []
        for y in range(image.height()):
            row: list[int] = []
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                luminance = 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()
                row.append(1 if luminance < 200 and color.alpha() > 0 else 0)
            matrix.append(row)
        return matrix


class AppController:
    def __init__(
        self,
        gemini_client: OpenAICompatibleClient,
        stroke_planner: StrokePlanner,
        preview_renderer: PreviewRenderer,
        draw_executor: DrawExecutor,
        line_art_matrix_factory: LineArtMatrixFactory | None = None,
    ) -> None:
        self.gemini_client = gemini_client
        self.image_generation_client = gemini_client
        self.stroke_planner = stroke_planner
        self.preview_renderer = preview_renderer
        self.draw_executor = draw_executor
        self.line_art_matrix_factory = line_art_matrix_factory or LineArtMatrixFactory()
        self.session = ExecutionSession()

    def load_image(self, image_path: str) -> None:
        self.session.image_path = image_path
        self.session.status = "image_loaded"

    def generate_line_art(self) -> None:
        if not self.session.image_path:
            raise RuntimeError("No image has been selected.")
        self.session.line_art = self.image_generation_client.generate_line_art(self.session.image_path)
        self.session.status = "line_art_ready"

    def set_region(self, region: CalibrationRegion) -> None:
        self.session.active_region = region
        if self.session.status == "idle":
            self.session.status = "region_ready"

    def prepare_preview(self) -> object:
        if self.session.line_art is None:
            raise RuntimeError("Line art is not ready.")
        if self.session.active_region is None:
            raise RuntimeError("Calibration region is required.")

        matrix = self.line_art_matrix_factory.from_line_art(self.session.line_art)
        plan = self.stroke_planner.plan(matrix, self.session.active_region)
        self.session.stroke_plan = plan
        preview = self.preview_renderer.render(self.session.line_art, plan, self.session.active_region)
        self.session.last_preview = preview
        self.session.status = "ready"
        return preview

    def start_drawing(self) -> None:
        if self.session.stroke_plan is None:
            raise RuntimeError("Preview must be prepared first.")
        self.draw_executor.start(self.session)

    def cancel(self) -> None:
        self.draw_executor.cancel()
        self.session.cancel("hotkey")

    @property
    def current_plan(self) -> StrokePlan | None:
        return self.session.stroke_plan
