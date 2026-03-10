import threading
import unittest
from gc import collect
from pathlib import Path
from unittest.mock import patch

from sts_draw.app_controller import AppController
from sts_draw.canvas_calibrator import CanvasCalibrator
from sts_draw.image_generation_client import OpenAICompatibleClient, OpenAICompatibleSettings
from sts_draw.models import CalibrationRegion, LineArtResult, PreviewPlacementResult
from sts_draw.preview_renderer import PreviewRenderer
from sts_draw.stroke_planner import StrokePlanner
from sts_draw.ui import MainWindowFactory


class FakeHotkeys:
    def __init__(self) -> None:
        self.mapping = None
        self.register_calls: list[dict[str, object]] = []

    def register(self, mapping) -> None:
        self.mapping = mapping
        self.register_calls.append(dict(mapping))


class FakeDrawExecutor:
    def start(self, session) -> None:
        session.status = "running"

    def cancel(self) -> None:
        return None


class FakeMimeData:
    def __init__(self, urls=None) -> None:
        self._urls = urls or []

    def hasUrls(self) -> bool:
        return bool(self._urls)

    def urls(self):
        return self._urls


class FakeClipboard:
    def __init__(self, mime_data=None, text: str = "", image=None) -> None:
        self._mime_data = mime_data or FakeMimeData()
        self._text = text
        self._image = image

    def mimeData(self):
        return self._mime_data

    def text(self) -> str:
        return self._text

    def image(self):
        return self._image


class FakePlacementCalibrator:
    def __init__(self) -> None:
        self.called_thread_ids: list[int] = []
        self.placement = PreviewPlacementResult(
            region=CalibrationRegion(left=10, top=20, width=30, height=40),
            scale=1.25,
        )
        self.raise_error: Exception | None = None
        self.last_line_art = None
        self.last_initial_scale = None

    def place_preview(self, line_art, initial_scale: float = 1.0):
        self.called_thread_ids.append(threading.get_ident())
        self.last_line_art = line_art
        self.last_initial_scale = initial_scale
        if self.raise_error is not None:
            raise self.raise_error
        return self.placement


class MainWindowFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6 import QtWidgets

        cls.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.temp_dir = Path(__file__).resolve().parent / "_tmp_ui_tests"
        cls.temp_dir.mkdir(exist_ok=True)
        cls.png_bytes = cls._make_png_bytes()

    @classmethod
    def _make_png_bytes(cls) -> bytes:
        from PySide6 import QtCore, QtGui

        image = QtGui.QImage(8, 8, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("white"))
        image.setPixelColor(2, 2, QtGui.QColor("black"))
        image.setPixelColor(5, 5, QtGui.QColor("black"))
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(buffer.data())

    def tearDown(self) -> None:
        from PySide6 import QtWidgets

        for widget in list(QtWidgets.QApplication.topLevelWidgets()):
            widget.close()
            widget.deleteLater()
        self.qt_app.processEvents()
        collect()
        for path in list(self.temp_dir.glob("*")):
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                self.qt_app.processEvents()
                collect()
                path.unlink(missing_ok=True)

    def test_window_uses_preview_over_control_layout(self) -> None:
        window = self._build_window()

        self.assertEqual(window.windowTitle(), "STS 绘图助手")
        self.assertEqual(window.root_layout.objectName(), "root_layout")
        self.assertEqual(window.root_layout.count(), 2)
        self.assertEqual(window.preview_panel.objectName(), "preview_panel")
        self.assertEqual(window.control_panel.objectName(), "control_panel")

    def test_window_exposes_preview_widgets_and_provider_inputs(self) -> None:
        window = self._build_window()

        self.assertEqual(window.original_preview_label.objectName(), "original_preview")
        self.assertEqual(window.line_art_preview_label.objectName(), "line_art_preview")
        self.assertEqual(window.shortcut_card.objectName(), "shortcut_panel")
        self.assertEqual(window.api_key_input.placeholderText(), "输入 OpenRouter 或兼容服务的 API Key")
        self.assertEqual(window.model_input.placeholderText(), "输入模型名称")
        self.assertEqual(window.base_url_input.placeholderText(), "输入接口地址")
        self.assertEqual(window.proxy_input.placeholderText(), "输入 HTTP(S) 代理地址")
        self.assertEqual(window.fill_proxy_button.text(), "填入 7890")
        self.assertEqual(window.hotkey_value_labels["stop"].text(), "Ctrl+Alt+S")
        self.assertEqual(window.mouse_button_combo.currentData(), "left")
        self.assertTrue(window.status_value_label.wordWrap())
        self.assertEqual(window.preview_button.text(), "定位预览")
        self.assertFalse(hasattr(window, "calibrate_button"))

    def test_fill_proxy_button_updates_only_proxy_input(self) -> None:
        window = self._build_window()

        window.proxy_input.clear()
        window._controller.image_generation_client.settings.proxy_url = None
        window.fill_proxy_button.click()

        self.assertEqual(window.proxy_input.text(), "http://127.0.0.1:7890")
        self.assertIsNone(window._controller.image_generation_client.settings.proxy_url)

    def test_original_preview_is_clickable_import_entry(self) -> None:
        window = self._build_window()

        self.assertTrue(hasattr(window.original_preview_label, "clicked"))
        self.assertIsNone(getattr(window, "browse_button", None))

    def test_window_registers_ctrl_v_shortcut(self) -> None:
        from PySide6 import QtCore

        window = self._build_window()

        self.assertEqual(window.paste_shortcut.key().toString(), "Ctrl+V")
        self.assertEqual(window.paste_shortcut.context(), QtCore.Qt.ApplicationShortcut)

    def test_ctrl_v_in_focused_input_still_pastes_plain_text(self) -> None:
        from PySide6 import QtCore, QtGui

        window = self._build_window()
        event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_V,
            QtCore.Qt.ControlModifier,
            "v",
        )

        with patch(
            "PySide6.QtWidgets.QApplication.clipboard",
            return_value=FakeClipboard(text="plain-text-token", image=QtGui.QImage()),
        ):
            handled = window.eventFilter(window.api_key_input, event)

        self.assertFalse(handled)
        self.assertIsNone(window._controller.session.image_path)

    def test_ctrl_v_in_focused_input_imports_file_url_image(self) -> None:
        from PySide6 import QtCore, QtGui

        window = self._build_window()
        image_path = self.temp_dir / "focused-url.png"
        image_path.write_bytes(self.png_bytes)
        event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_V,
            QtCore.Qt.ControlModifier,
            "v",
        )

        clipboard = FakeClipboard(
            text=QtCore.QUrl.fromLocalFile(str(image_path)).toString(),
            image=QtGui.QImage(),
        )
        with patch("PySide6.QtWidgets.QApplication.clipboard", return_value=clipboard):
            handled = window.eventFilter(window.api_key_input, event)

        self.assertTrue(handled)
        self.assertEqual(window._controller.session.image_path, str(image_path))

    def test_ctrl_v_in_focused_input_imports_image_instead_of_text(self) -> None:
        from PySide6 import QtCore, QtGui, QtWidgets

        window = self._build_window()
        image_path = self.temp_dir / "focused-image.png"
        image_path.write_bytes(self.png_bytes)
        clipboard = QtWidgets.QApplication.clipboard()
        clipboard.setText("should-not-paste")
        window._clipboard_to_temp_image = lambda: str(image_path)
        event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_V,
            QtCore.Qt.ControlModifier,
            "v",
        )

        handled = window.eventFilter(window.api_key_input, event)

        self.assertTrue(handled)
        self.assertEqual(window._controller.session.image_path, str(image_path))

    def test_clicking_original_preview_opens_file_picker_flow(self) -> None:
        window = self._build_window()
        calls: list[str] = []
        window._browse_image = lambda: calls.append("browse")

        window.original_preview_label.clicked.emit()

        self.assertEqual(calls, ["browse"])

    def test_generate_line_art_updates_provider_settings_and_preview(self) -> None:
        window = self._build_window()
        controller = window._controller

        window.api_key_input.setText("sk-test")
        window.model_input.setText("google/gemini-custom")
        window.base_url_input.setText("https://openrouter.ai/api/v1")
        window.proxy_input.setText("http://127.0.0.1:7890")
        controller.load_image("image.png")
        controller.session.line_art = LineArtResult(
            image_bytes=self.png_bytes,
            mime_type="image/png",
            width=8,
            height=8,
        )
        controller.generate_line_art = lambda: None

        window._generate_line_art()

        self.assertEqual(controller.image_generation_client.settings.api_key, "sk-test")
        self.assertEqual(controller.image_generation_client.settings.model, "google/gemini-custom")
        self.assertEqual(controller.image_generation_client.settings.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(controller.image_generation_client.settings.proxy_url, "http://127.0.0.1:7890")
        self.assertEqual(window.status_value_label.text(), "线稿已生成")
        self.assertFalse(window.line_art_preview_label.pixmap().isNull())

    def test_position_preview_updates_region_scale_and_preview_summary(self) -> None:
        calibrator = FakePlacementCalibrator()
        window = self._build_window(calibrator=calibrator)
        controller = window._controller
        controller.session.line_art = LineArtResult(
            image_bytes=self.png_bytes,
            mime_type="image/png",
            width=8,
            height=8,
        )
        controller.prepare_preview = lambda: {"segment_count": 9}

        window._preview()

        self.assertIs(calibrator.last_line_art, controller.session.line_art)
        self.assertEqual(calibrator.last_initial_scale, 1.0)
        self.assertEqual(controller.session.active_region, calibrator.placement.region)
        self.assertEqual(controller.session.preview_scale, calibrator.placement.scale)
        self.assertIn("30", window.region_value_label.text())
        self.assertIn("40", window.region_value_label.text())
        self.assertEqual(window.preview_value_label.text(), "9 段路径")

    def test_cancelled_position_preview_keeps_existing_region_and_preview_state(self) -> None:
        calibrator = FakePlacementCalibrator()
        calibrator.raise_error = RuntimeError("Preview placement was cancelled.")
        window = self._build_window(calibrator=calibrator)
        controller = window._controller
        controller.session.line_art = LineArtResult(
            image_bytes=self.png_bytes,
            mime_type="image/png",
            width=8,
            height=8,
        )
        existing_region = CalibrationRegion(left=1, top=2, width=3, height=4)
        controller.session.active_region = existing_region
        controller.session.preview_scale = 2.0
        window.region_value_label.setText("3 × 4")
        window.preview_value_label.setText("7 段路径")

        window._preview()

        self.assertEqual(controller.session.active_region, existing_region)
        self.assertEqual(controller.session.preview_scale, 2.0)
        self.assertEqual(window.region_value_label.text(), "3 × 4")
        self.assertEqual(window.preview_value_label.text(), "7 段路径")

    def test_position_preview_reuses_session_scale_as_initial_scale(self) -> None:
        calibrator = FakePlacementCalibrator()
        window = self._build_window(calibrator=calibrator)
        controller = window._controller
        controller.session.line_art = LineArtResult(
            image_bytes=self.png_bytes,
            mime_type="image/png",
            width=8,
            height=8,
        )
        controller.session.preview_scale = 2.5
        controller.prepare_preview = lambda: {"segment_count": 1}

        window._preview()

        self.assertEqual(calibrator.last_initial_scale, 2.5)

    def test_recording_hotkey_updates_session_persists_and_reregisters(self) -> None:
        from PySide6 import QtCore, QtGui
        from sts_draw.user_settings import UserSettingsStore

        window = self._build_window()
        window._begin_hotkey_recording("stop")

        event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_X,
            QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier,
            "x",
        )
        window.keyPressEvent(event)

        self.assertEqual(window._controller.session.hotkeys["stop"], "ctrl+alt+x")
        self.assertEqual(window.hotkey_value_labels["stop"].text(), "Ctrl+Alt+X")
        self.assertIn("ctrl+alt+x", window._hotkeys_manager.mapping)
        saved = UserSettingsStore(window._settings_store.path).load()
        self.assertEqual(saved.hotkeys["stop"], "ctrl+alt+x")

    def test_recording_alt_shift_hotkey_shows_conflict_warning(self) -> None:
        from PySide6 import QtCore, QtGui

        window = self._build_window()
        window._begin_hotkey_recording("calibrate")

        event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_Q,
            QtCore.Qt.AltModifier | QtCore.Qt.ShiftModifier,
            "q",
        )
        window.keyPressEvent(event)

        self.assertEqual(window._controller.session.hotkeys["calibrate"], "alt+shift+q")
        self.assertIn("Alt+Shift", window.status_value_label.text())

    def test_duplicate_hotkey_is_rejected(self) -> None:
        from PySide6 import QtCore, QtGui

        window = self._build_window()
        original_stop_hotkey = window._controller.session.hotkeys["stop"]
        window._begin_hotkey_recording("stop")

        event = QtGui.QKeyEvent(
            QtCore.QEvent.KeyPress,
            QtCore.Qt.Key_C,
            QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier,
            "c",
        )
        window.keyPressEvent(event)

        self.assertEqual(window._controller.session.hotkeys["stop"], original_stop_hotkey)
        self.assertIn("已在使用", window.status_value_label.text())

    def test_mouse_button_selection_updates_session_and_persists(self) -> None:
        from sts_draw.user_settings import UserSettingsStore

        window = self._build_window()

        window.mouse_button_combo.setCurrentIndex(1)

        self.assertEqual(window._controller.session.draw_mouse_button, "right")
        saved = UserSettingsStore(window._settings_store.path).load()
        self.assertEqual(saved.draw_mouse_button, "right")

    def test_loading_new_image_clears_previous_line_art_preview(self) -> None:
        window = self._build_window()
        window._set_line_art_preview(
            LineArtResult(image_bytes=self.png_bytes, mime_type="image/png", width=8, height=8)
        )
        image_path = self.temp_dir / "sample.png"
        image_path.write_bytes(self.png_bytes)

        window._load_image_from_path(str(image_path))

        self.assertTrue(window.line_art_preview_label.pixmap() is None or window.line_art_preview_label.pixmap().isNull())
        self.assertEqual(window.preview_value_label.text(), "未生成")

    def test_paste_from_clipboard_loads_image_and_updates_original_preview(self) -> None:
        window = self._build_window()
        image_path = self.temp_dir / "clipboard.png"
        image_path.write_bytes(self.png_bytes)
        window._clipboard_to_temp_image = lambda: str(image_path)

        window._paste_image_from_clipboard()

        self.assertTrue(window._controller.session.image_path.endswith("clipboard.png"))
        self.assertEqual(window.status_value_label.text(), "已从剪贴板导入图片")
        self.assertFalse(window.original_preview_label.pixmap().isNull())

    def test_paste_from_clipboard_without_image_keeps_current_state(self) -> None:
        window = self._build_window()
        window.image_name_value_label.setText("existing.png")
        window._clipboard_to_temp_image = lambda: None

        window._paste_image_from_clipboard()

        self.assertEqual(window.status_value_label.text(), "剪贴板中没有可用图片")
        self.assertEqual(window.image_name_value_label.text(), "existing.png")

    def test_clipboard_text_file_url_resolves_local_image_path(self) -> None:
        from PySide6 import QtCore, QtGui

        window = self._build_window()
        image_path = self.temp_dir / "qq-image.png"
        image_path.write_bytes(self.png_bytes)
        clipboard = FakeClipboard(
            text=QtCore.QUrl.fromLocalFile(str(image_path)).toString(),
            image=QtGui.QImage(),
        )

        with patch("PySide6.QtWidgets.QApplication.clipboard", return_value=clipboard):
            resolved = window._clipboard_to_temp_image()

        self.assertEqual(resolved, str(image_path))

    def test_clipboard_urls_prefers_first_local_image_path(self) -> None:
        from PySide6 import QtCore, QtGui

        window = self._build_window()
        text_path = self.temp_dir / "note.txt"
        text_path.write_text("x", encoding="utf-8")
        image_path = self.temp_dir / "clipboard.webp"
        image_path.write_bytes(self.png_bytes)
        clipboard = FakeClipboard(
            mime_data=FakeMimeData(
                [
                    QtCore.QUrl.fromLocalFile(str(text_path)),
                    QtCore.QUrl.fromLocalFile(str(image_path)),
                ]
            ),
            image=QtGui.QImage(),
        )

        with patch("PySide6.QtWidgets.QApplication.clipboard", return_value=clipboard):
            resolved = window._clipboard_to_temp_image()

        self.assertEqual(resolved, str(image_path))

    def test_clipboard_text_non_image_file_returns_none(self) -> None:
        from PySide6 import QtCore, QtGui

        window = self._build_window()
        text_path = self.temp_dir / "note.txt"
        text_path.write_text("x", encoding="utf-8")
        clipboard = FakeClipboard(
            text=QtCore.QUrl.fromLocalFile(str(text_path)).toString(),
            image=QtGui.QImage(),
        )

        with patch("PySide6.QtWidgets.QApplication.clipboard", return_value=clipboard):
            resolved = window._clipboard_to_temp_image()

        self.assertIsNone(resolved)

    def test_close_event_allows_window_to_close(self) -> None:
        from PySide6 import QtGui

        window = self._build_window()
        event = QtGui.QCloseEvent()

        window.closeEvent(event)

        self.assertTrue(event.isAccepted())

    def test_calibrate_hotkey_dispatches_back_to_main_thread(self) -> None:
        calibrator = FakePlacementCalibrator()
        window = self._build_window(calibrator=calibrator)
        window._controller.session.line_art = LineArtResult(
            image_bytes=self.png_bytes,
            mime_type="image/png",
            width=8,
            height=8,
        )
        window._controller.prepare_preview = lambda: {"segment_count": 3}
        main_thread_id = threading.get_ident()
        hotkey = window._controller.session.hotkeys["calibrate"]

        worker = threading.Thread(target=window._hotkeys_manager.mapping[hotkey])
        worker.start()
        worker.join()
        self.qt_app.processEvents()

        self.assertEqual(calibrator.called_thread_ids, [main_thread_id])
        self.assertIn("30", window.region_value_label.text())
        self.assertIn("40", window.region_value_label.text())
        self.assertEqual(window.preview_value_label.text(), "3 段路径")

    def test_start_hotkey_dispatches_back_to_main_thread(self) -> None:
        window = self._build_window()
        calls: list[int] = []
        main_thread_id = threading.get_ident()

        def fake_start() -> None:
            calls.append(threading.get_ident())

        window._start = fake_start
        window._register_hotkeys()
        hotkey = window._controller.session.hotkeys["start"]

        worker = threading.Thread(target=window._hotkeys_manager.mapping[hotkey])
        worker.start()
        worker.join()
        self.qt_app.processEvents()

        self.assertEqual(calls, [main_thread_id])

    def test_stop_hotkey_dispatches_back_to_main_thread(self) -> None:
        window = self._build_window()
        calls: list[int] = []
        main_thread_id = threading.get_ident()

        def fake_cancel() -> None:
            calls.append(threading.get_ident())

        window._controller.cancel = fake_cancel
        window._register_hotkeys()
        hotkey = window._controller.session.hotkeys["stop"]

        worker = threading.Thread(target=window._hotkeys_manager.mapping[hotkey])
        worker.start()
        worker.join()
        self.qt_app.processEvents()

        self.assertEqual(calls, [main_thread_id])

    def _build_window(self, calibrator=None):
        from sts_draw.user_settings import UserSettingsStore

        controller = AppController(
            gemini_client=OpenAICompatibleClient(settings=OpenAICompatibleSettings(api_key="existing")),
            stroke_planner=StrokePlanner(),
            preview_renderer=PreviewRenderer(),
            draw_executor=FakeDrawExecutor(),
        )
        controller.image_generation_client = controller.gemini_client
        settings_store = UserSettingsStore(self.temp_dir / "settings.json")
        hotkeys = FakeHotkeys()
        window = MainWindowFactory().create(
            controller,
            calibrator or CanvasCalibrator(),
            hotkeys,
            settings_store=settings_store,
        )
        window._controller = controller
        window._hotkeys_manager = hotkeys
        window._settings_store = settings_store
        return window


if __name__ == "__main__":
    unittest.main()
