import unittest

from sts_draw.models import CalibrationRegion
from sts_draw.stroke_planner import StrokePlanner


class StrokePlannerTests(unittest.TestCase):
    def test_generates_pen_lift_between_disconnected_runs(self) -> None:
        planner = StrokePlanner()
        matrix = [
            [0, 1, 1, 0],
            [0, 0, 0, 0],
            [1, 1, 0, 0],
        ]

        plan = planner.plan(matrix, CalibrationRegion(left=10, top=20, width=40, height=30))

        self.assertEqual(len(plan.segments), 4)
        self.assertFalse(plan.segments[0].pen_down)
        self.assertTrue(plan.segments[1].pen_down)
        self.assertFalse(plan.segments[2].pen_down)
        self.assertTrue(plan.segments[3].pen_down)
        self.assertEqual(plan.segments[0].end, (20, 20))
        self.assertEqual(plan.segments[1].end, (30, 20))
        self.assertEqual(plan.segments[2].end, (10, 40))
        self.assertEqual(plan.segments[3].end, (20, 40))

    def test_rejects_empty_input(self) -> None:
        planner = StrokePlanner()

        with self.assertRaises(ValueError):
            planner.plan([], CalibrationRegion(left=0, top=0, width=10, height=10))


if __name__ == "__main__":
    unittest.main()
