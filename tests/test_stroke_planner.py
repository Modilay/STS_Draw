import unittest

from sts_draw.models import CalibrationRegion
from sts_draw.stroke_planner import StrokePlanner


class StrokePlannerTests(unittest.TestCase):
    def test_reduces_thick_horizontal_band_to_single_centerline_stroke(self) -> None:
        planner = StrokePlanner()
        matrix = [
            [0, 0, 0, 0, 0],
            [1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1],
            [0, 0, 0, 0, 0],
        ]

        plan = planner.plan(matrix, CalibrationRegion(left=0, top=0, width=50, height=50))

        self.assertEqual(len(plan.segments), 2)
        self.assertFalse(plan.segments[0].pen_down)
        self.assertTrue(plan.segments[1].pen_down)
        self.assertEqual(plan.segments[0].end[1], plan.segments[1].start[1])
        self.assertEqual(plan.segments[1].start[1], plan.segments[1].end[1])
        self.assertGreater(plan.segments[1].end[0], plan.segments[1].start[0])

    def test_filters_isolated_noise_component(self) -> None:
        planner = StrokePlanner()
        matrix = [
            [0, 0, 0, 0, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 1, 1, 1, 0],
            [0, 0, 0, 0, 1],
        ]

        plan = planner.plan(matrix, CalibrationRegion(left=0, top=0, width=50, height=50))

        self.assertEqual(len(plan.segments), 2)
        self.assertFalse(plan.segments[0].pen_down)
        self.assertTrue(plan.segments[1].pen_down)
        self.assertLess(plan.segments[1].end[0], 50)

    def test_preserves_t_junction_branches_as_multiple_strokes(self) -> None:
        planner = StrokePlanner()
        matrix = [
            [0, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 1, 1, 0, 0],
            [0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0],
        ]

        plan = planner.plan(matrix, CalibrationRegion(left=0, top=0, width=70, height=70))

        draw_segments = [segment for segment in plan.segments if segment.pen_down]

        self.assertGreaterEqual(len(draw_segments), 2)
        self.assertTrue(any(segment.start[1] == segment.end[1] for segment in draw_segments))
        self.assertTrue(any(segment.start[0] == segment.end[0] for segment in draw_segments))

    def test_rejects_empty_input(self) -> None:
        planner = StrokePlanner()

        with self.assertRaises(ValueError):
            planner.plan([], CalibrationRegion(left=0, top=0, width=10, height=10))


if __name__ == "__main__":
    unittest.main()
