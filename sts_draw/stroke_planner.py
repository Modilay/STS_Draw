from __future__ import annotations

from dataclasses import dataclass
from math import acos, degrees, dist

from sts_draw.models import BezierStroke, CalibrationRegion, LineStroke, MoveStroke, Stroke, StrokePlan


Point = tuple[int, int]


@dataclass(slots=True)
class PlannerSettings:
    speed_pixels_per_second: int = 250
    min_component_pixels: int = 3
    min_path_pixels: int = 2
    simplify_tolerance: float = 0.75
    line_fit_tolerance: float = 0.9
    curve_fit_tolerance: float = 15.0
    min_curve_points: int = 4
    max_line_length_ratio: float = 1.05
    max_curve_length_ratio: float = 1.15
    max_curve_turn_degrees: float = 60.0
    closed_span_ratio_threshold: float = 0.45


class StrokePlanner:
    def __init__(self, settings: PlannerSettings | None = None) -> None:
        self.settings = settings or PlannerSettings()

    def plan(self, matrix: list[list[int]], region: CalibrationRegion) -> StrokePlan:
        if not matrix or not matrix[0]:
            raise ValueError("Line art matrix must not be empty.")

        rows = len(matrix)
        cols = len(matrix[0])
        if any(len(row) != cols for row in matrix):
            raise ValueError("Line art matrix must be rectangular.")

        cleaned = _remove_small_components(matrix, self.settings.min_component_pixels)
        skeleton = _zhang_suen_thinning(cleaned)
        paths = _trace_skeleton_paths(skeleton)
        filtered_paths = [path for path in paths if len(path) >= self.settings.min_path_pixels]
        ordered_paths = _order_paths(filtered_paths)

        x_step = region.width / cols
        y_step = region.height / rows
        segments: list[Stroke] = []

        for path in ordered_paths:
            screen_points = [
                self._to_screen_point(col_index, row_index, x_step, y_step, cols, rows, region)
                for col_index, row_index in path
            ]
            screen_points = _dedupe_consecutive_points(screen_points)
            if len(screen_points) < 2:
                continue

            start = screen_points[0]
            segments.append(MoveStroke(point=start))
            segments.extend(self._fit_strokes(screen_points))

        return StrokePlan(segments=segments, source_size=(cols, rows), region=region)

    def _fit_strokes(self, points: list[Point]) -> list[Stroke]:
        if len(points) < 2:
            return []
        strokes = self._fit_stroke_range(points)
        first_draw_seen = False
        for stroke in strokes:
            if isinstance(stroke, (LineStroke, BezierStroke)):
                stroke.continues_path = first_draw_seen
                first_draw_seen = True
        return strokes

    def _fit_stroke_range(self, points: list[Point]) -> list[Stroke]:
        points = _dedupe_consecutive_points(points)
        if len(points) < 2:
            return []
        if len(points) == 2:
            return [LineStroke(start=points[0], end=points[1], speed_pixels_per_second=self.settings.speed_pixels_per_second)]

        line_error = _max_line_error(points)
        line_length_ratio = _line_length_ratio(points)
        if (
            line_error <= self.settings.line_fit_tolerance
            and line_length_ratio <= self.settings.max_line_length_ratio
        ):
            return [LineStroke(start=points[0], end=points[-1], speed_pixels_per_second=self.settings.speed_pixels_per_second)]

        if len(points) >= self.settings.min_curve_points:
            analysis_points = _simplify_path(points, self.settings.simplify_tolerance)
            if (
                not _is_near_closed_span(points, self.settings.closed_span_ratio_threshold)
                and not _has_disallowed_curve_turns(analysis_points, self.settings.max_curve_turn_degrees)
            ):
                bezier = _fit_bezier(points, self.settings.speed_pixels_per_second)
                bezier_error = _max_bezier_error(points, bezier)
                bezier_length_ratio = _bezier_length_ratio(points, bezier)
                if (
                    bezier_error <= self.settings.curve_fit_tolerance
                    and bezier_error < line_error * 0.85
                    and bezier_length_ratio <= self.settings.max_curve_length_ratio
                    and _bezier_within_expanded_bounds(points, bezier, self.settings.curve_fit_tolerance)
                ):
                    return [bezier]

        split_index = _best_split_index(points)
        if split_index <= 0 or split_index >= len(points) - 1:
            return self._fallback_line_strokes(points)

        left = self._fit_stroke_range(points[: split_index + 1])
        right = self._fit_stroke_range(points[split_index:])
        if not left or not right:
            return self._fallback_line_strokes(points)
        return left + right

    def _fallback_line_strokes(self, points: list[Point]) -> list[Stroke]:
        tolerance = max(self.settings.simplify_tolerance / 2, 0.25)
        simplified = _simplify_path(points, tolerance)
        simplified = _dedupe_consecutive_points(simplified)
        if len(simplified) <= 2 and len(points) > 2 and _line_length_ratio(points) > self.settings.max_line_length_ratio:
            simplified = points

        strokes: list[Stroke] = []
        for point_a, point_b in zip(simplified, simplified[1:]):
            if point_a == point_b:
                continue
            strokes.append(
                LineStroke(
                    start=point_a,
                    end=point_b,
                    speed_pixels_per_second=self.settings.speed_pixels_per_second,
                )
            )
        return strokes

    @staticmethod
    def _to_screen_point(
        col_index: int,
        row_index: int,
        x_step: float,
        y_step: float,
        cols: int,
        rows: int,
        region: CalibrationRegion,
    ) -> tuple[int, int]:
        max_x_offset = max(region.width - 1, 0)
        max_y_offset = max(region.height - 1, 0)
        x_offset = min(round((col_index + 0.5) * x_step), max_x_offset)
        y_offset = min(round((row_index + 0.5) * y_step), max_y_offset)
        return region.left + x_offset, region.top + y_offset


def _remove_small_components(matrix: list[list[int]], min_pixels: int) -> list[list[int]]:
    if min_pixels <= 1:
        return [row[:] for row in matrix]

    rows = len(matrix)
    cols = len(matrix[0])
    kept = [[0 for _ in range(cols)] for _ in range(rows)]
    seen: set[Point] = set()

    for row_index in range(rows):
        for col_index in range(cols):
            point = (col_index, row_index)
            if matrix[row_index][col_index] != 1 or point in seen:
                continue
            component = _collect_component(matrix, point, seen)
            if len(component) < min_pixels:
                continue
            for component_col, component_row in component:
                kept[component_row][component_col] = 1

    return kept


def _collect_component(matrix: list[list[int]], start: Point, seen: set[Point]) -> list[Point]:
    rows = len(matrix)
    cols = len(matrix[0])
    stack = [start]
    component: list[Point] = []

    while stack:
        col_index, row_index = stack.pop()
        if (col_index, row_index) in seen:
            continue
        seen.add((col_index, row_index))
        if matrix[row_index][col_index] != 1:
            continue
        component.append((col_index, row_index))
        for next_col, next_row in _neighbors((col_index, row_index), cols, rows):
            if matrix[next_row][next_col] == 1 and (next_col, next_row) not in seen:
                stack.append((next_col, next_row))

    return component


def _zhang_suen_thinning(matrix: list[list[int]]) -> list[list[int]]:
    thin_pixels = {
        (col_index, row_index)
        for row_index, row in enumerate(matrix)
        for col_index, cell in enumerate(row)
        if cell == 1 and _is_thin_stroke_pixel(matrix, col_index, row_index)
    }
    skeleton = _pad_matrix(matrix)
    rows = len(skeleton)
    cols = len(skeleton[0])
    changed = True

    while changed:
        changed = False
        for step in (0, 1):
            to_remove: list[Point] = []
            for row_index in range(1, rows - 1):
                for col_index in range(1, cols - 1):
                    if skeleton[row_index][col_index] != 1:
                        continue
                    if not _should_remove_in_thinning(skeleton, col_index, row_index, step):
                        continue
                    to_remove.append((col_index, row_index))
            if not to_remove:
                continue
            changed = True
            for col_index, row_index in to_remove:
                skeleton[row_index][col_index] = 0

    unpadded = [row[1:-1] for row in skeleton[1:-1]]
    for col_index, row_index in thin_pixels:
        unpadded[row_index][col_index] = 1
    return unpadded


def _should_remove_in_thinning(matrix: list[list[int]], col_index: int, row_index: int, step: int) -> bool:
    p2 = matrix[row_index - 1][col_index]
    p3 = matrix[row_index - 1][col_index + 1]
    p4 = matrix[row_index][col_index + 1]
    p5 = matrix[row_index + 1][col_index + 1]
    p6 = matrix[row_index + 1][col_index]
    p7 = matrix[row_index + 1][col_index - 1]
    p8 = matrix[row_index][col_index - 1]
    p9 = matrix[row_index - 1][col_index - 1]
    neighbors = [p2, p3, p4, p5, p6, p7, p8, p9]

    neighbor_sum = sum(neighbors)
    if neighbor_sum < 2 or neighbor_sum > 6:
        return False
    if _transition_count(neighbors) != 1:
        return False

    if step == 0:
        return p2 * p4 * p6 == 0 and p4 * p6 * p8 == 0
    return p2 * p4 * p8 == 0 and p2 * p6 * p8 == 0


def _transition_count(neighbors: list[int]) -> int:
    return sum(
        1
        for current, next_value in zip(neighbors, neighbors[1:] + neighbors[:1])
        if current == 0 and next_value == 1
    )


def _trace_skeleton_paths(matrix: list[list[int]]) -> list[list[Point]]:
    rows = len(matrix)
    cols = len(matrix[0])
    points = {
        (col_index, row_index)
        for row_index, row in enumerate(matrix)
        for col_index, cell in enumerate(row)
        if cell == 1
    }
    if not points:
        return []

    neighbors_map = {
        point: [neighbor for neighbor in _neighbors(point, cols, rows) if neighbor in points]
        for point in points
    }
    keypoints = {point for point, neighbors in neighbors_map.items() if len(neighbors) != 2}
    visited_edges: set[tuple[Point, Point]] = set()
    paths: list[list[Point]] = []

    for point in sorted(keypoints):
        for neighbor in neighbors_map[point]:
            edge = _edge_key(point, neighbor)
            if edge in visited_edges:
                continue
            path = _walk_path(point, neighbor, keypoints, neighbors_map, visited_edges)
            if len(path) >= 2:
                paths.append(path)

    for point in sorted(points):
        for neighbor in neighbors_map[point]:
            edge = _edge_key(point, neighbor)
            if edge in visited_edges:
                continue
            path = _walk_loop(point, neighbor, neighbors_map, visited_edges)
            if len(path) >= 2:
                paths.append(path)

    return paths


def _walk_path(
    start: Point,
    next_point: Point,
    keypoints: set[Point],
    neighbors_map: dict[Point, list[Point]],
    visited_edges: set[tuple[Point, Point]],
) -> list[Point]:
    path = [start, next_point]
    visited_edges.add(_edge_key(start, next_point))
    previous = start
    current = next_point

    while current not in keypoints:
        candidates = [neighbor for neighbor in neighbors_map[current] if neighbor != previous]
        if not candidates:
            break
        chosen = candidates[0]
        visited_edges.add(_edge_key(current, chosen))
        path.append(chosen)
        previous, current = current, chosen

    return path


def _walk_loop(
    start: Point,
    next_point: Point,
    neighbors_map: dict[Point, list[Point]],
    visited_edges: set[tuple[Point, Point]],
) -> list[Point]:
    path = [start, next_point]
    visited_edges.add(_edge_key(start, next_point))
    previous = start
    current = next_point

    while True:
        candidates = [neighbor for neighbor in neighbors_map[current] if neighbor != previous]
        if not candidates:
            break
        chosen = next((neighbor for neighbor in candidates if _edge_key(current, neighbor) not in visited_edges), None)
        if chosen is None:
            break
        visited_edges.add(_edge_key(current, chosen))
        path.append(chosen)
        previous, current = current, chosen
        if current == start:
            break

    return path


def _order_paths(paths: list[list[Point]]) -> list[list[Point]]:
    if not paths:
        return []

    remaining = [path[:] for path in paths]
    ordered = [remaining.pop(0)]

    while remaining:
        tail = ordered[-1][-1]
        best_index = 0
        best_reversed = False
        best_distance: float | None = None

        for index, path in enumerate(remaining):
            forward_distance = dist(tail, path[0])
            backward_distance = dist(tail, path[-1])
            candidate_distance = min(forward_distance, backward_distance)
            if best_distance is None or candidate_distance < best_distance:
                best_distance = candidate_distance
                best_index = index
                best_reversed = backward_distance < forward_distance

        next_path = remaining.pop(best_index)
        if best_reversed:
            next_path.reverse()
        ordered.append(next_path)

    return ordered


def _simplify_path(path: list[Point], tolerance: float) -> list[Point]:
    if len(path) <= 2 or tolerance <= 0:
        return path[:]

    is_closed = path[0] == path[-1]
    working_path = path[:-1] if is_closed else path
    simplified = _rdp(working_path, tolerance)
    if is_closed and simplified[0] != simplified[-1]:
        simplified.append(simplified[0])
    return _dedupe_consecutive_points(simplified)


def _fit_bezier(points: list[Point], speed_pixels_per_second: int) -> BezierStroke:
    start = points[0]
    end = points[-1]
    chord_length = dist(start, end)
    start_tangent = _estimate_tangent(points, from_start=True)
    end_tangent = _estimate_tangent(points, from_start=False)
    start_handle = max(min(_polyline_length(points[: min(len(points), 4)]), max(chord_length, 1.0)), 1.0) / 3
    end_handle = max(min(_polyline_length(points[-min(len(points), 4):]), max(chord_length, 1.0)), 1.0) / 3
    control1 = (
        round(start[0] + start_tangent[0] * start_handle),
        round(start[1] + start_tangent[1] * start_handle),
    )
    control2 = (
        round(end[0] - end_tangent[0] * end_handle),
        round(end[1] - end_tangent[1] * end_handle),
    )
    return BezierStroke(
        start=start,
        control1=control1,
        control2=control2,
        end=end,
        speed_pixels_per_second=speed_pixels_per_second,
    )


def _max_line_error(points: list[Point]) -> float:
    return max((_distance_to_segment(point, points[0], points[-1]) for point in points[1:-1]), default=0.0)


def _max_bezier_error(points: list[Point], bezier: BezierStroke) -> float:
    if len(points) <= 2:
        return 0.0
    sampled = _sampled_bezier_polyline(bezier, max(len(points) * 4, 16))
    max_error = 0.0
    for point in points[1:-1]:
        max_error = max(max_error, _distance_to_polyline(point, sampled))
    return max_error


def _bezier_length_ratio(points: list[Point], bezier: BezierStroke) -> float:
    source_length = _polyline_length(points)
    if source_length <= 0:
        return 1.0
    return _polyline_length(_sampled_bezier_polyline(bezier, max(len(points) * 4, 16))) / source_length


def _bezier_within_expanded_bounds(points: list[Point], bezier: BezierStroke, padding: float) -> bool:
    sampled = _sampled_bezier_polyline(bezier, max(len(points) * 4, 16))
    min_x = min(point[0] for point in points) - padding
    max_x = max(point[0] for point in points) + padding
    min_y = min(point[1] for point in points) - padding
    max_y = max(point[1] for point in points) + padding
    return all(min_x <= point[0] <= max_x and min_y <= point[1] <= max_y for point in sampled)


def _sampled_bezier_polyline(bezier: BezierStroke, steps: int) -> list[Point]:
    return [bezier.start] + bezier.sample_points(steps=steps)


def _estimate_tangent(points: list[Point], from_start: bool) -> tuple[float, float]:
    if len(points) < 2:
        return (1.0, 0.0)
    origin = points[0] if from_start else points[-1]
    offsets = points[1 : min(len(points), 4)] if from_start else list(reversed(points[max(len(points) - 4, 0) : -1]))
    sum_x = 0.0
    sum_y = 0.0
    for point in offsets:
        if from_start:
            sum_x += point[0] - origin[0]
            sum_y += point[1] - origin[1]
        else:
            sum_x += origin[0] - point[0]
            sum_y += origin[1] - point[1]
    if sum_x == 0 and sum_y == 0:
        fallback = (
            points[-1][0] - points[0][0],
            points[-1][1] - points[0][1],
        )
        return _normalize_vector(fallback)
    return _normalize_vector((sum_x, sum_y))


def _normalize_vector(vector: tuple[float, float]) -> tuple[float, float]:
    length = dist((0.0, 0.0), vector)
    if length <= 0:
        return (1.0, 0.0)
    return (vector[0] / length, vector[1] / length)


def _line_length_ratio(points: list[Point]) -> float:
    chord_length = dist(points[0], points[-1])
    if chord_length <= 0:
        return float("inf")
    return _polyline_length(points) / chord_length


def _polyline_length(points: list[Point]) -> float:
    return sum(dist(point_a, point_b) for point_a, point_b in zip(points, points[1:]))


def _is_near_closed_span(points: list[Point], threshold: float) -> bool:
    polyline_length = _polyline_length(points)
    if polyline_length <= 0:
        return False
    return (dist(points[0], points[-1]) / polyline_length) < threshold


def _has_disallowed_curve_turns(points: list[Point], max_turn_degrees: float) -> bool:
    if len(points) < 3:
        return False

    last_turn_sign = 0
    for index in range(1, len(points) - 1):
        turn_degrees = _turn_magnitude_degrees(points[index - 1], points[index], points[index + 1])
        if turn_degrees > max_turn_degrees:
            return True

        sign = _turn_sign(points[index - 1], points[index], points[index + 1])
        if sign == 0 or turn_degrees < 10.0:
            continue
        if last_turn_sign != 0 and sign != last_turn_sign:
            return True
        last_turn_sign = sign
    return False


def _turn_sign(previous: Point, current: Point, next_point: Point) -> int:
    vector_a = (current[0] - previous[0], current[1] - previous[1])
    vector_b = (next_point[0] - current[0], next_point[1] - current[1])
    cross = (vector_a[0] * vector_b[1]) - (vector_a[1] * vector_b[0])
    if abs(cross) < 1e-6:
        return 0
    return 1 if cross > 0 else -1


def _best_split_index(points: list[Point]) -> int:
    best_index = len(points) // 2
    largest_turn = -1.0
    for index in range(1, len(points) - 1):
        turn = _turn_magnitude_degrees(points[index - 1], points[index], points[index + 1])
        if turn > largest_turn:
            largest_turn = turn
            best_index = index
    return best_index


def _turn_angle(previous: Point, current: Point, next_point: Point) -> float:
    vector_a = (previous[0] - current[0], previous[1] - current[1])
    vector_b = (next_point[0] - current[0], next_point[1] - current[1])
    length_a = dist((0.0, 0.0), vector_a)
    length_b = dist((0.0, 0.0), vector_b)
    if length_a == 0 or length_b == 0:
        return 0.0
    cosine = ((vector_a[0] * vector_b[0]) + (vector_a[1] * vector_b[1])) / (length_a * length_b)
    cosine = max(-1.0, min(1.0, cosine))
    return acos(cosine)


def _turn_magnitude_degrees(previous: Point, current: Point, next_point: Point) -> float:
    return max(0.0, 180.0 - degrees(_turn_angle(previous, current, next_point)))


def _rdp(path: list[Point], tolerance: float) -> list[Point]:
    if len(path) <= 2:
        return path[:]

    start = path[0]
    end = path[-1]
    best_index = -1
    best_distance = -1.0

    for index in range(1, len(path) - 1):
        candidate_distance = _distance_to_segment(path[index], start, end)
        if candidate_distance > best_distance:
            best_distance = candidate_distance
            best_index = index

    if best_distance <= tolerance:
        return [start, end]

    left = _rdp(path[: best_index + 1], tolerance)
    right = _rdp(path[best_index:], tolerance)
    return left[:-1] + right


def _distance_to_segment(point: Point, start: Point, end: Point) -> float:
    if start == end:
        return dist(point, start)

    start_x, start_y = start
    end_x, end_y = end
    point_x, point_y = point
    dx = end_x - start_x
    dy = end_y - start_y
    projection = ((point_x - start_x) * dx + (point_y - start_y) * dy) / (dx * dx + dy * dy)
    projection = max(0.0, min(1.0, projection))
    projected = (start_x + projection * dx, start_y + projection * dy)
    return dist(point, projected)


def _distance_to_polyline(point: Point, polyline: list[Point]) -> float:
    if not polyline:
        return 0.0
    if len(polyline) == 1:
        return dist(point, polyline[0])
    return min(_distance_to_segment(point, point_a, point_b) for point_a, point_b in zip(polyline, polyline[1:]))


def _dedupe_consecutive_points(points: list[Point]) -> list[Point]:
    if not points:
        return []
    deduped = [points[0]]
    for point in points[1:]:
        if point != deduped[-1]:
            deduped.append(point)
    return deduped


def _pad_matrix(matrix: list[list[int]]) -> list[list[int]]:
    cols = len(matrix[0])
    padded = [[0 for _ in range(cols + 2)]]
    for row in matrix:
        padded.append([0] + row[:] + [0])
    padded.append([0 for _ in range(cols + 2)])
    return padded


def _is_thin_stroke_pixel(matrix: list[list[int]], col_index: int, row_index: int) -> bool:
    rows = len(matrix)
    cols = len(matrix[0])
    for row_offset in (-1, 0):
        for col_offset in (-1, 0):
            top = row_index + row_offset
            left = col_index + col_offset
            if top < 0 or left < 0 or top + 1 >= rows or left + 1 >= cols:
                continue
            block_sum = (
                matrix[top][left]
                + matrix[top][left + 1]
                + matrix[top + 1][left]
                + matrix[top + 1][left + 1]
            )
            if block_sum == 4:
                return False
    return True


def _edge_key(point_a: Point, point_b: Point) -> tuple[Point, Point]:
    return tuple(sorted((point_a, point_b)))


def _neighbors(point: Point, cols: int, rows: int) -> list[Point]:
    col_index, row_index = point
    neighbors: list[Point] = []
    for row_offset in (-1, 0, 1):
        for col_offset in (-1, 0, 1):
            if row_offset == 0 and col_offset == 0:
                continue
            next_col = col_index + col_offset
            next_row = row_index + row_offset
            if 0 <= next_col < cols and 0 <= next_row < rows:
                neighbors.append((next_col, next_row))
    return neighbors
