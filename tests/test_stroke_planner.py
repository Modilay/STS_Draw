import unittest
from math import dist

from sts_draw.models import BezierStroke, CalibrationRegion, LineStroke, MoveStroke
from sts_draw.stroke_planner import StrokePlanner, _max_bezier_error


class StrokePlannerTests(unittest.TestCase):
    def test_near_closed_arc_is_not_collapsed_into_single_bezier(self) -> None:
        planner = StrokePlanner()
        matrix = [
            [0, 1, 1, 1, 0],
            [1, 0, 0, 0, 1],
            [1, 0, 0, 0, 0],
            [1, 0, 0, 0, 1],
            [0, 1, 1, 1, 0],
        ]

        plan = planner.plan(matrix, CalibrationRegion(left=0, top=0, width=50, height=50))

        draw_segments = [segment for segment in plan.segments if isinstance(segment, (LineStroke, BezierStroke))]
        self.assertGreater(len(draw_segments), 1)

    def test_bezier_error_uses_distance_to_curve_not_sample_index_alignment(self) -> None:
        stroke = BezierStroke(
            start=(0, 0),
            control1=(0, 50),
            control2=(100, 50),
            end=(100, 0),
        )
        points = [
            (0, 0),
            (0, 3),
            (1, 7),
            (3, 14),
            (9, 22),
            (22, 31),
            (50, 38),
            (81, 30),
            (97, 13),
            (100, 4),
            (100, 0),
        ]

        self.assertLess(_max_bezier_error(points, stroke), 2.0)

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
        self.assertIsInstance(plan.segments[0], MoveStroke)
        self.assertIsInstance(plan.segments[1], LineStroke)
        self.assertFalse(plan.segments[1].continues_path)
        self.assertEqual(plan.segments[0].point[1], plan.segments[1].start[1])
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
        self.assertIsInstance(plan.segments[0], MoveStroke)
        self.assertIsInstance(plan.segments[1], LineStroke)
        self.assertFalse(plan.segments[1].continues_path)
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

        draw_segments = [segment for segment in plan.segments if isinstance(segment, (LineStroke, BezierStroke))]

        self.assertGreaterEqual(len(draw_segments), 2)
        self.assertTrue(any(isinstance(segment, LineStroke) and segment.start[1] == segment.end[1] for segment in draw_segments))
        self.assertTrue(any(isinstance(segment, LineStroke) and segment.start[0] == segment.end[0] for segment in draw_segments))

    def test_smooth_point_path_emits_bezier_stroke(self) -> None:
        planner = StrokePlanner()
        points = [
            (0, 20),
            (10, 15),
            (20, 11),
            (30, 8),
            (40, 6),
            (50, 5),
            (60, 6),
            (70, 8),
            (80, 11),
            (90, 15),
            (100, 20),
        ]

        strokes = planner._fit_stroke_range(points)

        self.assertTrue(any(isinstance(segment, BezierStroke) for segment in strokes))

    def test_segments_after_first_draw_segment_continue_same_path(self) -> None:
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

        move_indices = [index for index, segment in enumerate(plan.segments) if isinstance(segment, MoveStroke)]
        draw_indices = [index for index, segment in enumerate(plan.segments) if isinstance(segment, (LineStroke, BezierStroke))]
        self.assertGreaterEqual(len(draw_indices), 2)
        for move_index in move_indices:
            next_index = move_index + 1
            if next_index >= len(plan.segments):
                continue
            next_segment = plan.segments[next_index]
            if isinstance(next_segment, (LineStroke, BezierStroke)):
                self.assertFalse(next_segment.continues_path)
                if next_index + 1 < len(plan.segments):
                    following = plan.segments[next_index + 1]
                    if isinstance(following, (LineStroke, BezierStroke)):
                        self.assertTrue(following.continues_path)

    def test_sharp_corner_is_not_collapsed_into_single_bezier(self) -> None:
        planner = StrokePlanner()
        matrix = [
            [1, 0, 0, 0, 0],
            [1, 0, 0, 0, 0],
            [1, 1, 1, 1, 1],
            [0, 0, 0, 0, 1],
            [0, 0, 0, 0, 1],
        ]

        plan = planner.plan(matrix, CalibrationRegion(left=0, top=0, width=50, height=50))

        draw_segments = [segment for segment in plan.segments if isinstance(segment, (LineStroke, BezierStroke))]
        self.assertGreaterEqual(len(draw_segments), 2)
        self.assertFalse(len(draw_segments) == 1 and isinstance(draw_segments[0], BezierStroke))

    def test_rasterized_plan_stays_close_to_near_closed_arc_source(self) -> None:
        planner = StrokePlanner()
        matrix = [
            [0, 1, 1, 1, 0],
            [1, 0, 0, 0, 1],
            [1, 0, 0, 0, 0],
            [1, 0, 0, 0, 1],
            [0, 1, 1, 1, 0],
        ]

        plan = planner.plan(matrix, CalibrationRegion(left=0, top=0, width=50, height=50))

        original_pixels = {
            (col_index, row_index)
            for row_index, row in enumerate(matrix)
            for col_index, value in enumerate(row)
            if value == 1
        }
        rasterized_pixels = _sample_plan_pixels(plan)
        extra_ratio = len(rasterized_pixels - original_pixels) / len(original_pixels)
        missing_ratio = len(original_pixels - rasterized_pixels) / len(original_pixels)

        self.assertLessEqual(extra_ratio, 0.3)
        self.assertLessEqual(missing_ratio, 0.3)

    def test_rejects_empty_input(self) -> None:
        planner = StrokePlanner()

        with self.assertRaises(ValueError):
            planner.plan([], CalibrationRegion(left=0, top=0, width=10, height=10))


def _sample_plan_pixels(plan) -> set[tuple[int, int]]:
    region = plan.region
    source_width, source_height = plan.source_size
    sampled_pixels: set[tuple[int, int]] = set()

    for segment in plan.segments:
        if isinstance(segment, LineStroke):
            points = _sample_line_points(segment.start, segment.end)
        elif isinstance(segment, BezierStroke):
            points = [segment.start] + segment.sample_points(steps=64)
        else:
            continue

        for point in points:
            source_point = (
                round((point[0] - region.left) * source_width / region.width - 0.5),
                round((point[1] - region.top) * source_height / region.height - 0.5),
            )
            sampled_pixels.add(
                (
                    min(max(source_point[0], 0), source_width - 1),
                    min(max(source_point[1], 0), source_height - 1),
                )
            )

    return sampled_pixels


def _sample_line_points(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    sample_count = max(int(dist(start, end) / 2), 1)
    points: list[tuple[int, int]] = []
    for index in range(sample_count + 1):
        progress = index / sample_count
        points.append(
            (
                round(start[0] + (end[0] - start[0]) * progress),
                round(start[1] + (end[1] - start[1]) * progress),
            )
        )
    return points


if __name__ == "__main__":
    unittest.main()
