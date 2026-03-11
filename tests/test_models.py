import unittest

from sts_draw.models import BezierStroke, CalibrationRegion, ExecutionSession, LineArtResult, LineStroke, MoveStroke


class CalibrationRegionTests(unittest.TestCase):
    def test_maps_normalized_point_into_screen_space(self) -> None:
        region = CalibrationRegion(left=100, top=50, width=400, height=200)

        point = region.map_point((0.25, 0.5))

        self.assertEqual(point, (200, 150))


class StrokeTests(unittest.TestCase):
    def test_line_stroke_estimates_duration_from_distance_and_speed(self) -> None:
        segment = LineStroke(start=(0, 0), end=(300, 400), speed_pixels_per_second=250)

        self.assertEqual(segment.estimated_duration_ms, 2000)
        self.assertFalse(segment.continues_path)

    def test_move_stroke_has_no_duration(self) -> None:
        stroke = MoveStroke(point=(12, 34))

        self.assertEqual(stroke.estimated_duration_ms, 0)

    def test_bezier_stroke_estimates_non_zero_duration(self) -> None:
        stroke = BezierStroke(
            start=(0, 0),
            control1=(20, 40),
            control2=(40, 40),
            end=(60, 0),
            speed_pixels_per_second=120,
        )

        self.assertGreater(stroke.estimated_duration_ms, 0)
        self.assertFalse(stroke.continues_path)


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

    def test_defaults_draw_speed_profile_to_balanced(self) -> None:
        session = ExecutionSession()

        self.assertEqual(session.draw_speed_profile, "balanced")

    def test_defaults_pause_hotkey_to_ctrl_alt_p(self) -> None:
        session = ExecutionSession()

        self.assertEqual(session.hotkeys["pause"], "ctrl+alt+p")
        self.assertIn("pause", session.hotkey_statuses)


class LineArtResultTests(unittest.TestCase):
    def test_reports_dimensions_from_bytes(self) -> None:
        result = LineArtResult(image_bytes=b"abc", mime_type="image/png", width=10, height=20)

        self.assertEqual(result.size, (10, 20))


if __name__ == "__main__":
    unittest.main()
