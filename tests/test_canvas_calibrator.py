import unittest

from sts_draw.canvas_calibrator import PreviewPlacementState


class PreviewPlacementStateTests(unittest.TestCase):
    def test_confirm_returns_region_from_cursor_center_and_scale(self) -> None:
        state = PreviewPlacementState(line_art_size=(100, 60), initial_scale=1.5)
        state.move_to(250, 180)

        placement = state.confirm()

        self.assertEqual(placement.scale, 1.5)
        self.assertEqual(placement.region.left, 175)
        self.assertEqual(placement.region.top, 135)
        self.assertEqual(placement.region.width, 150)
        self.assertEqual(placement.region.height, 90)

    def test_scaling_clamps_to_supported_range(self) -> None:
        state = PreviewPlacementState(line_art_size=(100, 60), initial_scale=1.0)

        state.apply_wheel_steps(-100)
        self.assertEqual(state.scale, 0.1)

        state.apply_wheel_steps(100)
        self.assertEqual(state.scale, 8.0)


if __name__ == "__main__":
    unittest.main()
