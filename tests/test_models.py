import unittest

from sts_draw.models import CalibrationRegion, ExecutionSession, LineArtResult, StrokeSegment


class CalibrationRegionTests(unittest.TestCase):
    def test_maps_normalized_point_into_screen_space(self) -> None:
        region = CalibrationRegion(left=100, top=50, width=400, height=200)

        point = region.map_point((0.25, 0.5))

        self.assertEqual(point, (200, 150))


class StrokeSegmentTests(unittest.TestCase):
    def test_estimates_duration_from_distance_and_speed(self) -> None:
        segment = StrokeSegment(start=(0, 0), end=(300, 400), pen_down=True, speed_pixels_per_second=250)

        self.assertEqual(segment.estimated_duration_ms, 2000)


class ExecutionSessionTests(unittest.TestCase):
    def test_cancellation_sets_status_and_reason(self) -> None:
        session = ExecutionSession()

        session.cancel("hotkey")

        self.assertEqual(session.status, "cancelled")
        self.assertEqual(session.error_reason, "hotkey")

    def test_defaults_to_left_mouse_button(self) -> None:
        session = ExecutionSession()

        self.assertEqual(session.draw_mouse_button, "left")

    def test_defaults_preview_scale_to_none(self) -> None:
        session = ExecutionSession()

        self.assertIsNone(session.preview_scale)


class LineArtResultTests(unittest.TestCase):
    def test_reports_dimensions_from_bytes(self) -> None:
        result = LineArtResult(image_bytes=b"abc", mime_type="image/png", width=10, height=20)

        self.assertEqual(result.size, (10, 20))


if __name__ == "__main__":
    unittest.main()
