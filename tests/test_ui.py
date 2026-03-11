import threading
import time
import unittest
from gc import collect
from pathlib import Path
from unittest.mock import patch

from sts_draw.app_controller import AppController
from sts_draw.canvas_calibrator import CanvasCalibrator
from sts_draw.global_hotkeys import HotkeyCheckResult
from sts_draw.image_generation_client import OpenAICompatibleClient, OpenAICompatibleSettings
from sts_draw.models import CalibrationRegion, LineArtResult, PreviewPlacementResult, StrokePlan, StrokeSegment
from sts_draw.preview_renderer import PreviewRenderer
from sts_draw.stroke_planner import StrokePlanner
from sts_draw.ui import MainWindowFactory


class FakeHotkeys:
    def __init__(self) -> None:
        self.mapping = None
        self.register_calls = []
        self.check_results = {}

    def register(self, mapping):
        results = {}
        active = {}
        for hotkey, callback in mapping.items():
            result = self.check_results.get(hotkey)
            results[hotkey] = result
            if result is None or result.ok:
                active[hotkey] = callback
        self.mapping = active
        self.register_calls.append(dict(mapping))
        return results

    def check_hotkey(self, hotkey: str):
        return self.check_results[hotkey]


class FakeDrawExecutor:
    def __init__(self) -> None:
        self.start_thread_ids = []
        self.cancel_thread_ids = []
        self.started = threading.Event()
        self.finish = threading.Event()

    def start(self, session, status_callback=None) -> None:
        self.start_thread_ids.append(threading.get_ident())
        self.started.set()
        if status_callback is not None:
            status_callback('running')
        session.status = 'running'
        self.finish.wait(timeout=2)
        if session.status == 'running':
            session.status = 'completed'
            if status_callback is not None:
                status_callback('completed')

    def cancel(self) -> None:
        self.cancel_thread_ids.append(threading.get_ident())


class FakeMimeData:
    def __init__(self, urls=None) -> None:
        self._urls = urls or []

    def hasUrls(self) -> bool:
        return bool(self._urls)

    def urls(self):
        return self._urls


class FakeClipboard:
    def __init__(self, mime_data=None, text='', image=None) -> None:
        self._mime_data = mime_data or FakeMimeData()
        self._text = text
        self._image = image

    def mimeData(self):
        return self._mime_data

    def text(self):
        return self._text

    def image(self):
        return self._image


class FakePlacementCalibrator:
    def __init__(self) -> None:
        self.called_thread_ids = []
        self.placement = PreviewPlacementResult(CalibrationRegion(10, 20, 30, 40), 1.25)
        self.raise_error = None
        self.last_line_art = None
        self.last_initial_scale = None

    def place_preview(self, line_art, initial_scale=1.0):
        self.called_thread_ids.append(threading.get_ident())
        self.last_line_art = line_art
        self.last_initial_scale = initial_scale
        if self.raise_error is not None:
            raise self.raise_error
        return self.placement


class FakeLineArtGenerator:
    def __init__(self, line_art: LineArtResult) -> None:
        self.line_art = line_art
        self.calls = 0
        self.thread_ids = []
        self.started = threading.Event()
        self.finish = threading.Event()
        self.error: Exception | None = None

    def __call__(self) -> None:
        self.calls += 1
        self.thread_ids.append(threading.get_ident())
        self.started.set()
        self.finish.wait(timeout=2)
        if self.error is not None:
            raise self.error


class MainWindowFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets, QtCore, QtGui

        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.temp_dir = Path(__file__).resolve().parent / '_tmp_ui_tests'
        cls.temp_dir.mkdir(exist_ok=True)
        image = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor('white'))
        image.setPixelColor(2, 2, QtGui.QColor('black'))
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        image.save(buffer, 'PNG')
        cls.png_bytes = bytes(buffer.data())

    def tearDown(self) -> None:
        from PySide6 import QtWidgets

        for widget in list(QtWidgets.QApplication.topLevelWidgets()):
            widget.close()
            widget.deleteLater()
        self.qt_app.processEvents()
        collect()
        for path in list(self.temp_dir.glob('*')):
            path.unlink(missing_ok=True)

    def test_window_has_expected_core_widgets(self) -> None:
        window = self._build_window()
        self.assertEqual(window.windowTitle(), 'STS 绘图助手')
        self.assertEqual(window.root_layout.objectName(), 'root_layout')
        self.assertEqual(window.preview_panel.objectName(), 'preview_panel')
        self.assertEqual(window.control_panel.objectName(), 'control_panel')
        self.assertEqual(window.original_preview_label.objectName(), 'original_preview')
        self.assertEqual(window.line_art_preview_label.objectName(), 'line_art_preview')
        self.assertEqual(window.shortcut_card.objectName(), 'shortcut_panel')
        self.assertEqual(window.hotkey_value_labels['stop'].text(), 'Ctrl+Alt+S')
        self.assertIn('stop', window.hotkey_status_labels)
        self.assertEqual(window.mouse_button_combo.currentData(), 'left')
        self.assertFalse(hasattr(window, 'calibrate_button'))

    def test_fill_proxy_button_updates_proxy_input_and_persists(self) -> None:
        from sts_draw.user_settings import UserSettingsStore

        window = self._build_window()
        window.proxy_input.clear()
        window._controller.image_generation_client.settings.proxy_url = None
        window.fill_proxy_button.click()

        saved = UserSettingsStore(window._settings_store.path).load()

        self.assertEqual(window.proxy_input.text(), 'http://127.0.0.1:7890')
        self.assertEqual(window._controller.image_generation_client.settings.proxy_url, 'http://127.0.0.1:7890')
        self.assertEqual(saved.proxy_url, 'http://127.0.0.1:7890')

    def test_ctrl_v_in_focused_input_still_pastes_plain_text(self) -> None:
        from PySide6 import QtCore, QtGui
        window = self._build_window()
        event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_V, QtCore.Qt.ControlModifier, 'v')
        with patch('PySide6.QtWidgets.QApplication.clipboard', return_value=FakeClipboard(text='plain', image=QtGui.QImage())):
            handled = window.eventFilter(window.api_key_input, event)
        self.assertFalse(handled)
        self.assertIsNone(window._controller.session.image_path)

    def test_clicking_original_preview_calls_browse(self) -> None:
        window = self._build_window()
        calls = []
        window._browse_image = lambda: calls.append('browse')
        window.original_preview_label.clicked.emit()
        self.assertEqual(calls, ['browse'])

    def test_generate_line_art_updates_settings_and_preview(self) -> None:
        window = self._build_window()
        controller = window._controller
        window.api_key_input.setText('sk-test')
        window.model_input.setText('google/gemini-custom')
        window.base_url_input.setText('https://openrouter.ai/api/v1')
        window.proxy_input.setText('http://127.0.0.1:7890')
        controller.load_image('image.png')
        generator = FakeLineArtGenerator(LineArtResult(self.png_bytes, 'image/png', 8, 8))

        def generate() -> None:
            generator()
            controller.session.line_art = generator.line_art

        controller.generate_line_art = generate
        window._generate_line_art()
        self.assertTrue(generator.started.wait(timeout=1))
        self.assertFalse(window.line_art_button.isEnabled())
        self.assertFalse(window.preview_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        self.assertEqual(window.status_value_label.text(), '正在生成线稿...')
        generator.finish.set()
        self._wait_for(lambda: window.line_art_button.isEnabled(), timeout=1)
        self.assertEqual(controller.image_generation_client.settings.api_key, 'sk-test')
        self.assertEqual(controller.image_generation_client.settings.model, 'google/gemini-custom')
        self.assertEqual(controller.image_generation_client.settings.base_url, 'https://openrouter.ai/api/v1')
        self.assertEqual(controller.image_generation_client.settings.proxy_url, 'http://127.0.0.1:7890')
        self.assertIsNotNone(controller.session.line_art)
        self.assertFalse(window.line_art_preview_label.pixmap().isNull())
        self.assertIn('线稿已生成', window.status_value_label.text())

    def test_generate_line_art_runs_in_background_thread(self) -> None:
        window = self._build_window()
        controller = window._controller
        controller.load_image('image.png')
        generator = FakeLineArtGenerator(LineArtResult(self.png_bytes, 'image/png', 8, 8))

        def generate() -> None:
            generator()
            controller.session.line_art = generator.line_art

        controller.generate_line_art = generate
        main_thread_id = threading.get_ident()

        window._generate_line_art()

        self.assertTrue(generator.started.wait(timeout=1))
        self.assertNotEqual(generator.thread_ids, [main_thread_id])
        generator.finish.set()
        self._wait_for(lambda: window._line_art_thread is None, timeout=1)

    def test_generate_line_art_failure_restores_controls_and_shows_error(self) -> None:
        window = self._build_window()
        controller = window._controller
        controller.load_image('image.png')
        generator = FakeLineArtGenerator(LineArtResult(self.png_bytes, 'image/png', 8, 8))
        generator.error = RuntimeError('Provider API key is missing.')
        controller.generate_line_art = generator

        window._generate_line_art()

        self.assertTrue(generator.started.wait(timeout=1))
        generator.finish.set()
        self._wait_for(lambda: window.line_art_button.isEnabled(), timeout=1)
        self.assertEqual(window.status_value_label.text(), '操作失败：Provider API key is missing.')
        self.assertIsNone(controller.session.line_art)

    def test_generate_line_art_ignores_reentrant_clicks(self) -> None:
        window = self._build_window()
        controller = window._controller
        controller.load_image('image.png')
        generator = FakeLineArtGenerator(LineArtResult(self.png_bytes, 'image/png', 8, 8))

        def generate() -> None:
            generator()
            controller.session.line_art = generator.line_art

        controller.generate_line_art = generate

        window._generate_line_art()
        self.assertTrue(generator.started.wait(timeout=1))
        window._generate_line_art()

        self.assertEqual(generator.calls, 1)
        self.assertEqual(window.status_value_label.text(), '正在生成线稿...')
        generator.finish.set()
        self._wait_for(lambda: window._line_art_thread is None, timeout=1)

    def test_window_restores_saved_runtime_settings(self) -> None:
        from sts_draw.user_settings import UserSettings, UserSettingsStore

        settings_store = UserSettingsStore(self.temp_dir / 'settings.json')
        settings_store.save(
            UserSettings(
                hotkeys={'calibrate': 'ctrl+alt+c', 'start': 'ctrl+alt+d', 'stop': 'ctrl+alt+s'},
                api_key='saved-key',
                proxy_url='http://127.0.0.1:7890',
                model='google/gemini-custom',
                base_url='https://example.com/v1',
            )
        )

        window = self._build_window(settings_store=settings_store)

        self.assertEqual(window.api_key_input.text(), 'saved-key')
        self.assertEqual(window.proxy_input.text(), 'http://127.0.0.1:7890')
        self.assertEqual(window.model_input.text(), 'google/gemini-custom')
        self.assertEqual(window.base_url_input.text(), 'https://example.com/v1')
        self.assertEqual(window._controller.image_generation_client.settings.api_key, 'saved-key')
        self.assertEqual(window._controller.image_generation_client.settings.proxy_url, 'http://127.0.0.1:7890')
        self.assertEqual(window._controller.image_generation_client.settings.model, 'google/gemini-custom')
        self.assertEqual(window._controller.image_generation_client.settings.base_url, 'https://example.com/v1')

    def test_api_key_input_persists_immediately(self) -> None:
        from sts_draw.user_settings import UserSettingsStore

        window = self._build_window()

        window.api_key_input.setText('sk-live')

        saved = UserSettingsStore(window._settings_store.path).load()

        self.assertEqual(window._controller.image_generation_client.settings.api_key, 'sk-live')
        self.assertEqual(saved.api_key, 'sk-live')

    def test_proxy_input_normalizes_empty_value_when_persisting(self) -> None:
        from sts_draw.user_settings import UserSettingsStore

        window = self._build_window()
        window.proxy_input.setText('http://127.0.0.1:7890')
        window.proxy_input.clear()

        saved = UserSettingsStore(window._settings_store.path).load()

        self.assertIsNone(window._controller.image_generation_client.settings.proxy_url)
        self.assertIsNone(saved.proxy_url)

    def test_preview_updates_region_scale_and_summary(self) -> None:
        calibrator = FakePlacementCalibrator()
        window = self._build_window(calibrator=calibrator)
        controller = window._controller
        controller.session.line_art = LineArtResult(self.png_bytes, 'image/png', 8, 8)
        controller.prepare_preview = lambda: {'segment_count': 9}
        window._preview()
        self.assertIs(calibrator.last_line_art, controller.session.line_art)
        self.assertEqual(calibrator.last_initial_scale, 1.0)
        self.assertEqual(controller.session.active_region, calibrator.placement.region)
        self.assertEqual(controller.session.preview_scale, calibrator.placement.scale)
        self.assertIn('30', window.region_value_label.text())
        self.assertIn('40', window.region_value_label.text())
        self.assertIn('9', window.preview_value_label.text())

    def test_cancelled_preview_keeps_existing_state(self) -> None:
        calibrator = FakePlacementCalibrator()
        calibrator.raise_error = RuntimeError('Preview placement was cancelled.')
        window = self._build_window(calibrator=calibrator)
        controller = window._controller
        controller.session.line_art = LineArtResult(self.png_bytes, 'image/png', 8, 8)
        controller.session.active_region = CalibrationRegion(1, 2, 3, 4)
        controller.session.preview_scale = 2.0
        window.region_value_label.setText('3 × 4')
        window.preview_value_label.setText('7 段路径')
        window._preview()
        self.assertEqual(controller.session.active_region, CalibrationRegion(1, 2, 3, 4))
        self.assertEqual(controller.session.preview_scale, 2.0)
        self.assertEqual(window.region_value_label.text(), '3 × 4')
        self.assertEqual(window.preview_value_label.text(), '7 段路径')

    def test_recording_hotkey_updates_session_and_reregisters(self) -> None:
        from PySide6 import QtCore, QtGui
        from sts_draw.user_settings import UserSettingsStore
        window = self._build_window()
        window._begin_hotkey_recording('stop')
        event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_X, QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier, 'x')
        window.keyPressEvent(event)
        self.assertEqual(window._controller.session.hotkeys['stop'], 'ctrl+alt+x')
        self.assertEqual(window.hotkey_value_labels['stop'].text(), 'Ctrl+Alt+X')
        self.assertIn('ctrl+alt+x', window._hotkeys_manager.mapping)
        saved = UserSettingsStore(window._settings_store.path).load()
        self.assertEqual(saved.hotkeys['stop'], 'ctrl+alt+x')

    def test_recording_conflicting_hotkey_marks_it_disabled_and_persists_value(self) -> None:
        from PySide6 import QtCore, QtGui
        from sts_draw.user_settings import UserSettingsStore

        hotkeys = FakeHotkeys()
        hotkeys.check_results['ctrl+alt+x'] = HotkeyCheckResult(
            hotkey='ctrl+alt+x',
            ok=False,
            conflict_reason='registration_failed',
            message='occupied',
        )
        window = self._build_window(hotkeys=hotkeys)
        window._begin_hotkey_recording('stop')
        event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_X, QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier, 'x')
        window.keyPressEvent(event)

        self.assertEqual(window._controller.session.hotkeys['stop'], 'ctrl+alt+x')
        self.assertIn('未生效', window.hotkey_status_labels['stop'].text())
        saved = UserSettingsStore(window._settings_store.path).load()
        self.assertEqual(saved.hotkeys['stop'], 'ctrl+alt+x')

    def test_startup_conflicting_hotkey_shows_disabled_state(self) -> None:
        from sts_draw.user_settings import UserSettings, UserSettingsStore

        settings_store = UserSettingsStore(self.temp_dir / 'settings.json')
        settings_store.save(UserSettings(hotkeys={'calibrate': 'ctrl+alt+c', 'start': 'ctrl+alt+d', 'stop': 'ctrl+alt+s'}))
        hotkeys = FakeHotkeys()
        hotkeys.check_results['ctrl+alt+s'] = HotkeyCheckResult(
            hotkey='ctrl+alt+s',
            ok=False,
            conflict_reason='registration_failed',
            message='occupied',
        )

        window = self._build_window(hotkeys=hotkeys, settings_store=settings_store)

        self.assertIn('未生效', window.hotkey_status_labels['stop'].text())
        self.assertNotIn('ctrl+alt+s', window._hotkeys_manager.mapping)

    def test_mouse_button_selection_updates_session_and_persists(self) -> None:
        from sts_draw.user_settings import UserSettingsStore
        window = self._build_window()
        window.mouse_button_combo.setCurrentIndex(1)
        self.assertEqual(window._controller.session.draw_mouse_button, 'right')
        saved = UserSettingsStore(window._settings_store.path).load()
        self.assertEqual(saved.draw_mouse_button, 'right')

    def test_calibrate_hotkey_dispatches_back_to_main_thread(self) -> None:
        calibrator = FakePlacementCalibrator()
        window = self._build_window(calibrator=calibrator)
        window._controller.session.line_art = LineArtResult(self.png_bytes, 'image/png', 8, 8)
        window._controller.prepare_preview = lambda: {'segment_count': 3}
        main_thread_id = threading.get_ident()
        hotkey = window._controller.session.hotkeys['calibrate']
        worker = threading.Thread(target=window._hotkeys_manager.mapping[hotkey])
        worker.start(); worker.join(); self.qt_app.processEvents()
        self.assertEqual(calibrator.called_thread_ids, [main_thread_id])
        self.assertIn('3', window.preview_value_label.text())

    def test_start_hotkey_dispatches_back_to_main_thread(self) -> None:
        window = self._build_window()
        calls = []
        main_thread_id = threading.get_ident()
        window._start = lambda: calls.append(threading.get_ident())
        window._register_hotkeys()
        hotkey = window._controller.session.hotkeys['start']
        worker = threading.Thread(target=window._hotkeys_manager.mapping[hotkey])
        worker.start(); worker.join(); self.qt_app.processEvents()
        self.assertEqual(calls, [main_thread_id])

    def test_stop_hotkey_cancels_immediately_without_waiting_for_main_thread(self) -> None:
        window = self._build_window()
        calls = []
        main_thread_id = threading.get_ident()
        window._controller.cancel = lambda: calls.append(threading.get_ident())
        window._register_hotkeys()
        hotkey = window._controller.session.hotkeys['stop']
        worker = threading.Thread(target=window._hotkeys_manager.mapping[hotkey])
        worker.start(); worker.join()
        self.assertEqual(len(calls), 1)
        self.assertNotEqual(calls[0], main_thread_id)

    def test_start_runs_drawing_in_background_thread(self) -> None:
        window = self._build_window()
        main_thread_id = threading.get_ident()
        window._controller.session.stroke_plan = self._make_stroke_plan()
        window._start()
        self.assertTrue(window._controller.draw_executor.started.wait(timeout=1))
        self.assertNotEqual(window._controller.draw_executor.start_thread_ids, [main_thread_id])
        window._controller.draw_executor.finish.set()
        self._wait_for(lambda: window._controller.session.status == 'completed', timeout=1)

    def test_stop_hotkey_updates_status_after_background_cancel(self) -> None:
        window = self._build_window()
        window._controller.session.stroke_plan = self._make_stroke_plan()
        window._start()
        self.assertTrue(window._controller.draw_executor.started.wait(timeout=1))
        hotkey = window._controller.session.hotkeys['stop']
        worker = threading.Thread(target=window._hotkeys_manager.mapping[hotkey])
        worker.start(); worker.join()
        window._controller.draw_executor.finish.set()
        self._wait_for(lambda: window._controller.session.status == 'cancelled', timeout=1)
        self.assertEqual(window.status_value_label.text(), '已停止')

    def _build_window(self, calibrator=None, draw_executor=None, hotkeys=None, settings_store=None):
        from sts_draw.user_settings import UserSettingsStore
        controller = AppController(
            gemini_client=OpenAICompatibleClient(settings=OpenAICompatibleSettings(api_key='existing')),
            stroke_planner=StrokePlanner(),
            preview_renderer=PreviewRenderer(),
            draw_executor=draw_executor or FakeDrawExecutor(),
        )
        settings_store = settings_store or UserSettingsStore(self.temp_dir / 'settings.json')
        hotkeys = hotkeys or FakeHotkeys()
        window = MainWindowFactory().create(controller, calibrator or CanvasCalibrator(), hotkeys, settings_store=settings_store)
        window._controller = controller
        window._hotkeys_manager = hotkeys
        window._settings_store = settings_store
        return window

    def _make_stroke_plan(self):
        return StrokePlan([StrokeSegment((0, 0), (10, 10), True)], (10, 10), CalibrationRegion(0, 0, 10, 10))

    def _wait_for(self, predicate, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.qt_app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail('Condition was not met before timeout.')


if __name__ == '__main__':
    unittest.main()
