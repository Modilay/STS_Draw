import threading
import time
import unittest
from ctypes import POINTER, cast
from unittest.mock import patch

from sts_draw import draw_executor
from sts_draw.draw_executor import DrawExecutor
from sts_draw.models import BezierStroke, CalibrationRegion, ExecutionSession, LineStroke, MoveStroke, StrokePlan


class FakeUser32:
    def __init__(self) -> None:
        self.positions: list[tuple[int, int]] = []
        self.events: list[int] = []
        self.inputs: list[tuple[int, int, int]] = []
        self.absolute_positions: list[tuple[int, int]] = []

    def SetCursorPos(self, x: int, y: int) -> None:
        self.positions.append((x, y))

    def mouse_event(self, event_flag: int, *_args) -> None:
        self.events.append(event_flag)
        return None

    def GetSystemMetrics(self, index: int) -> int:
        if index == 0:
            return 1920
        return 1080

    def SendInput(self, count: int, input_ptr, _size: int) -> int:
        self.assert_count(count)
        input_data = cast(input_ptr, POINTER(draw_executor._INPUT)).contents
        mouse_input = input_data.data.mouse_input
        self.inputs.append((mouse_input.dwFlags, mouse_input.dx, mouse_input.dy))
        if mouse_input.dwFlags & draw_executor.MOUSEEVENTF_MOVE:
            self.absolute_positions.append(
                (
                    round(mouse_input.dx * (self.GetSystemMetrics(0) - 1) / 65535),
                    round(mouse_input.dy * (self.GetSystemMetrics(1) - 1) / 65535),
                )
            )
        return count

    @staticmethod
    def assert_count(count: int) -> None:
        if count != 1:
            raise AssertionError(f"Expected a single INPUT record, got {count}.")


class DrawExecutorTests(unittest.TestCase):
    def test_profile_helper_returns_balanced_by_default_for_invalid_value(self) -> None:
        settings = draw_executor.executor_settings_for_profile("invalid")

        self.assertEqual(settings, draw_executor.executor_settings_for_profile("balanced"))

    def test_profile_helper_returns_expected_settings_for_each_profile(self) -> None:
        stable = draw_executor.executor_settings_for_profile("stable")
        balanced = draw_executor.executor_settings_for_profile("balanced")
        fast = draw_executor.executor_settings_for_profile("fast")

        self.assertEqual(
            stable,
            draw_executor.ExecutorSettings(
                drag_step_pixels=8,
                min_step_delay_ms=10,
                path_settle_ms=25,
                completion_settle_ms=120,
                move_step_pixels=12,
            ),
        )
        self.assertEqual(
            balanced,
            draw_executor.ExecutorSettings(
                drag_step_pixels=10,
                min_step_delay_ms=5,
                path_settle_ms=12,
                completion_settle_ms=45,
                move_step_pixels=16,
            ),
        )
        self.assertEqual(
            fast,
            draw_executor.ExecutorSettings(
                drag_step_pixels=12,
                min_step_delay_ms=2,
                path_settle_ms=6,
                completion_settle_ms=20,
                move_step_pixels=20,
            ),
        )

    def test_balanced_profile_has_lower_settle_cost_than_stable(self) -> None:
        stable = draw_executor.executor_settings_for_profile("stable")
        balanced = draw_executor.executor_settings_for_profile("balanced")

        self.assertLess(balanced.min_step_delay_ms, stable.min_step_delay_ms)
        self.assertLess(balanced.path_settle_ms, stable.path_settle_ms)
        self.assertLess(balanced.completion_settle_ms, stable.completion_settle_ms)
        self.assertGreaterEqual(balanced.drag_step_pixels, stable.drag_step_pixels)

    def test_exposes_executor_settings_for_conservative_drag_pacing(self) -> None:
        self.assertTrue(hasattr(draw_executor, "ExecutorSettings"))

    def test_long_segment_move_density_respects_drag_step_setting(self) -> None:
        self.assertTrue(hasattr(draw_executor, "ExecutorSettings"))
        settings = draw_executor.ExecutorSettings(
            drag_step_pixels=20,
            min_step_delay_ms=0,
            path_settle_ms=0,
            completion_settle_ms=0,
            move_step_pixels=20,
        )
        executor = DrawExecutor(settings=settings)
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(0, 0)),
                    LineStroke(start=(0, 0), end=(100, 0), speed_pixels_per_second=1000000),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            executor.start(session)

        move_inputs = [entry for entry in fake_user32.inputs if entry[0] & draw_executor.MOUSEEVENTF_MOVE]
        self.assertLessEqual(len(move_inputs), 6)

    def test_executor_waits_for_path_and_completion_settle_windows(self) -> None:
        self.assertTrue(hasattr(draw_executor, "ExecutorSettings"))
        settings = draw_executor.ExecutorSettings(
            drag_step_pixels=20,
            min_step_delay_ms=0,
            path_settle_ms=25,
            completion_settle_ms=120,
            move_step_pixels=20,
        )
        executor = DrawExecutor(settings=settings)
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(0, 0)),
                    LineStroke(start=(0, 0), end=(30, 0), speed_pixels_per_second=1000000),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()
        sleep_calls: list[float] = []

        def fake_sleep(seconds: float, _session, _status_callback) -> bool:
            sleep_calls.append(seconds)
            return False

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            with patch.object(executor, "_sleep_with_cancel", side_effect=fake_sleep):
                executor.start(session)

        rounded = {round(seconds, 3) for seconds in sleep_calls}
        self.assertIn(0.025, rounded)
        self.assertIn(0.12, rounded)

    def test_pen_down_segment_drags_from_start_to_end_in_multiple_steps(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(10, 20)),
                    LineStroke(start=(10, 20), end=(30, 40), speed_pixels_per_second=100),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            executor.start(session)

        self.assertEqual(session.status, "completed")
        move_inputs = [entry for entry in fake_user32.inputs if entry[0] & draw_executor.MOUSEEVENTF_MOVE]
        button_inputs = [entry[0] for entry in fake_user32.inputs if entry[0] & (draw_executor.MOUSEEVENTF_LEFTDOWN | draw_executor.MOUSEEVENTF_LEFTUP)]
        self.assertGreater(len(move_inputs), 2)
        self.assertEqual(button_inputs, [draw_executor.MOUSEEVENTF_LEFTDOWN, draw_executor.MOUSEEVENTF_LEFTUP])
        self.assertEqual(fake_user32.events, [])

    def test_bezier_stroke_draws_with_intermediate_points(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(10, 20)),
                    BezierStroke(
                        start=(10, 20),
                        control1=(30, 0),
                        control2=(50, 60),
                        end=(70, 20),
                        speed_pixels_per_second=100,
                    ),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            executor.start(session)

        self.assertEqual(session.status, "completed")
        move_inputs = [entry for entry in fake_user32.inputs if entry[0] & draw_executor.MOUSEEVENTF_MOVE]
        self.assertGreater(len(move_inputs), 3)
        self.assertTrue(any(entry[2] != move_inputs[0][2] for entry in move_inputs[1:-1]))

    def test_contiguous_segments_share_single_pen_down_and_pen_up(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(10, 10)),
                    LineStroke(start=(10, 10), end=(20, 20), speed_pixels_per_second=100),
                    LineStroke(start=(20, 20), end=(30, 30), speed_pixels_per_second=100, continues_path=True),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            executor.start(session)

        button_inputs = [entry[0] for entry in fake_user32.inputs if entry[0] & (draw_executor.MOUSEEVENTF_LEFTDOWN | draw_executor.MOUSEEVENTF_LEFTUP)]
        self.assertEqual(button_inputs, [draw_executor.MOUSEEVENTF_LEFTDOWN, draw_executor.MOUSEEVENTF_LEFTUP])

    def test_bezier_followed_by_line_keeps_pen_down(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(10, 20)),
                    BezierStroke(
                        start=(10, 20),
                        control1=(30, 0),
                        control2=(50, 60),
                        end=(70, 20),
                        speed_pixels_per_second=100,
                    ),
                    LineStroke(start=(70, 20), end=(90, 20), speed_pixels_per_second=100, continues_path=True),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            executor.start(session)

        button_inputs = [entry[0] for entry in fake_user32.inputs if entry[0] & (draw_executor.MOUSEEVENTF_LEFTDOWN | draw_executor.MOUSEEVENTF_LEFTUP)]
        self.assertEqual(button_inputs, [draw_executor.MOUSEEVENTF_LEFTDOWN, draw_executor.MOUSEEVENTF_LEFTUP])

    def test_cancel_stops_before_processing_all_segments(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    LineStroke(start=(0, 0), end=(100, 100), speed_pixels_per_second=1)
                    for _ in range(5)
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()
        thread = threading.Thread(target=executor.start, args=(session,))

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            thread.start()
            while session.status not in {"running", "cancelled", "completed"}:
                pass
            while (
                draw_executor.MOUSEEVENTF_LEFTDOWN
                not in [entry[0] for entry in fake_user32.inputs]
                and session.status not in {"cancelled", "completed"}
            ):
                pass
            executor.cancel()
            thread.join(timeout=2)

        self.assertEqual(session.status, "cancelled")
        self.assertLess(len(fake_user32.positions), len(session.stroke_plan.segments))

    def test_cancel_releases_mouse_when_continuous_path_is_active(self) -> None:
        executor = DrawExecutor()
        session = ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(0, 0)),
                    LineStroke(start=(0, 0), end=(100, 100), speed_pixels_per_second=1),
                    LineStroke(start=(100, 100), end=(200, 200), speed_pixels_per_second=1, continues_path=True),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )
        fake_user32 = FakeUser32()
        thread = threading.Thread(target=executor.start, args=(session,))

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            thread.start()
            while session.status not in {"running", "cancelled", "completed"}:
                pass
            while (
                draw_executor.MOUSEEVENTF_LEFTDOWN
                not in [entry[0] for entry in fake_user32.inputs]
                and session.status not in {"cancelled", "completed"}
            ):
                pass
            executor.cancel()
            thread.join(timeout=2)

        self.assertEqual(session.status, "cancelled")
        up_inputs = [entry for entry in fake_user32.inputs if entry[0] == draw_executor.MOUSEEVENTF_LEFTUP]
        self.assertGreaterEqual(len(up_inputs), 1)

    def test_pause_enters_paused_state_and_releases_mouse(self) -> None:
        settings = draw_executor.ExecutorSettings(
            drag_step_pixels=1,
            min_step_delay_ms=5,
            path_settle_ms=0,
            completion_settle_ms=0,
            move_step_pixels=20,
        )
        executor = DrawExecutor(settings=settings)
        session = self._make_long_running_session()
        fake_user32 = FakeUser32()
        thread = threading.Thread(target=executor.start, args=(session,))

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            thread.start()
            self._wait_for(lambda: session.status == "running", timeout=1)
            self._wait_for(lambda: len(fake_user32.absolute_positions) > 5, timeout=1)
            executor.pause()
            self._wait_for(lambda: session.status == "paused", timeout=1)
            executor.cancel()
            thread.join(timeout=2)

        button_inputs = [entry[0] for entry in fake_user32.inputs if entry[0] & (draw_executor.MOUSEEVENTF_LEFTDOWN | draw_executor.MOUSEEVENTF_LEFTUP)]
        self.assertIn(draw_executor.MOUSEEVENTF_LEFTUP, button_inputs)
        self.assertEqual(session.status, "cancelled")

    def test_resume_continues_from_paused_point_without_restarting_path(self) -> None:
        settings = draw_executor.ExecutorSettings(
            drag_step_pixels=1,
            min_step_delay_ms=5,
            path_settle_ms=0,
            completion_settle_ms=0,
            move_step_pixels=20,
        )
        executor = DrawExecutor(settings=settings)
        session = self._make_long_running_session()
        fake_user32 = FakeUser32()
        thread = threading.Thread(target=executor.start, args=(session,))

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            thread.start()
            self._wait_for(lambda: session.status == "running", timeout=1)
            self._wait_for(lambda: len(fake_user32.absolute_positions) > 5, timeout=1)
            executor.pause()
            self._wait_for(lambda: session.status == "paused", timeout=1)
            move_count_before_resume = len(fake_user32.absolute_positions)
            executor.resume()
            self._wait_for(
                lambda: session.status == "running" and len(fake_user32.absolute_positions) > move_count_before_resume + 3,
                timeout=1.5,
            )
            thread.join(timeout=2)

        self.assertEqual(session.status, "completed")
        later_positions = fake_user32.absolute_positions[move_count_before_resume:]
        self.assertTrue(later_positions)
        self.assertNotIn((0, 0), later_positions)
        button_inputs = [entry[0] for entry in fake_user32.inputs if entry[0] & (draw_executor.MOUSEEVENTF_LEFTDOWN | draw_executor.MOUSEEVENTF_LEFTUP)]
        self.assertEqual(
            button_inputs,
            [
                draw_executor.MOUSEEVENTF_LEFTDOWN,
                draw_executor.MOUSEEVENTF_LEFTUP,
                draw_executor.MOUSEEVENTF_LEFTDOWN,
                draw_executor.MOUSEEVENTF_LEFTUP,
            ],
        )

    def test_cancel_while_paused_ends_session_immediately(self) -> None:
        settings = draw_executor.ExecutorSettings(
            drag_step_pixels=1,
            min_step_delay_ms=5,
            path_settle_ms=0,
            completion_settle_ms=0,
            move_step_pixels=20,
        )
        executor = DrawExecutor(settings=settings)
        session = self._make_long_running_session()
        fake_user32 = FakeUser32()
        thread = threading.Thread(target=executor.start, args=(session,))

        with patch("ctypes.windll.user32", new=fake_user32, create=True):
            thread.start()
            self._wait_for(lambda: session.status == "running", timeout=1)
            self._wait_for(lambda: len(fake_user32.absolute_positions) > 5, timeout=1)
            executor.pause()
            self._wait_for(lambda: session.status == "paused", timeout=1)
            executor.cancel()
            thread.join(timeout=2)

        self.assertEqual(session.status, "cancelled")
        self.assertFalse(thread.is_alive())

    def _make_long_running_session(self) -> ExecutionSession:
        return ExecutionSession(
            stroke_plan=StrokePlan(
                segments=[
                    MoveStroke(point=(0, 0)),
                    LineStroke(start=(0, 0), end=(120, 0), speed_pixels_per_second=100000),
                ],
                source_size=(10, 10),
                region=CalibrationRegion(left=0, top=0, width=10, height=10),
            )
        )

    def _wait_for(self, predicate, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Condition was not met before timeout.")


if __name__ == "__main__":
    unittest.main()
