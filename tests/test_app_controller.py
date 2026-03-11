import unittest

from sts_draw.app_controller import AppController
from sts_draw.models import CalibrationRegion, LineArtResult, MoveStroke, StrokePlan


class FakeGeminiClient:
    def generate_line_art(self, image_path: str, prompt: str | None = None) -> LineArtResult:
        return LineArtResult(image_bytes=b"x", mime_type="image/png", width=8, height=8)


class FakePreviewRenderer:
    def render(self, line_art: LineArtResult, stroke_plan: StrokePlan, region: CalibrationRegion) -> dict:
        return {"segment_count": len(stroke_plan.segments), "region": region.bounds}


class FakeExecutor:
    def __init__(self) -> None:
        self.started = False
        self.pause_calls = 0
        self.resume_calls = 0

    def start(self, session) -> None:
        self.started = True
        session.status = "running"

    def pause(self) -> None:
        self.pause_calls += 1

    def resume(self) -> None:
        self.resume_calls += 1


class FakeStrokePlanner:
    def __init__(self) -> None:
        self.segments = [MoveStroke(point=(0, 0))]

    def plan(self, matrix, region):
        return StrokePlan(segments=self.segments, source_size=(1, 1), region=region)


class FakeMatrixFactory:
    def from_line_art(self, line_art):
        return [[1]]


class AppControllerTests(unittest.TestCase):
    def test_prepare_preview_requires_region(self) -> None:
        controller = AppController(
            gemini_client=FakeGeminiClient(),
            stroke_planner=FakeStrokePlanner(),
            preview_renderer=FakePreviewRenderer(),
            draw_executor=FakeExecutor(),
            line_art_matrix_factory=FakeMatrixFactory(),
        )
        controller.load_image("image.png")
        controller.generate_line_art()

        with self.assertRaises(RuntimeError):
            controller.prepare_preview()

    def test_prepare_preview_uses_services_and_updates_state(self) -> None:
        controller = AppController(
            gemini_client=FakeGeminiClient(),
            stroke_planner=FakeStrokePlanner(),
            preview_renderer=FakePreviewRenderer(),
            draw_executor=FakeExecutor(),
            line_art_matrix_factory=FakeMatrixFactory(),
        )
        controller.load_image("image.png")
        controller.generate_line_art()
        controller.set_region(CalibrationRegion(left=1, top=2, width=3, height=4))

        preview = controller.prepare_preview()

        self.assertEqual(controller.session.status, "ready")
        self.assertEqual(preview["segment_count"], 1)

    def test_toggle_pause_dispatches_based_on_session_status(self) -> None:
        executor = FakeExecutor()
        controller = AppController(
            gemini_client=FakeGeminiClient(),
            stroke_planner=FakeStrokePlanner(),
            preview_renderer=FakePreviewRenderer(),
            draw_executor=executor,
            line_art_matrix_factory=FakeMatrixFactory(),
        )

        controller.session.status = "running"
        controller.toggle_pause()
        controller.session.status = "paused"
        controller.toggle_pause()

        self.assertEqual(executor.pause_calls, 1)
        self.assertEqual(executor.resume_calls, 1)


class LineArtMatrixFactoryTests(unittest.TestCase):
    def test_reads_black_pixels_as_strokes(self) -> None:
        from PySide6 import QtCore, QtGui
        from sts_draw.app_controller import LineArtMatrixFactory

        image = QtGui.QImage(2, 2, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("white"))
        image.setPixelColor(0, 0, QtGui.QColor("black"))
        image.setPixelColor(1, 0, QtGui.QColor("black"))
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        result = LineArtResult(image_bytes=bytes(buffer.data()), mime_type="image/png", width=2, height=2)

        matrix = LineArtMatrixFactory().from_line_art(result)

        self.assertEqual(matrix, [[1, 1], [0, 0]])


if __name__ == "__main__":
    unittest.main()
