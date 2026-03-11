from __future__ import annotations

import tempfile
import threading
import uuid
from pathlib import Path

from sts_draw.app_controller import AppController
from sts_draw.canvas_calibrator import CanvasCalibrator
from sts_draw.draw_executor import executor_settings_for_profile
from sts_draw.global_hotkeys import GlobalHotkeyManager, HotkeyCheckResult
from sts_draw.models import HotkeyStatus
from sts_draw.user_settings import UserSettings, UserSettingsStore


class MainWindowFactory:
    def create(
        self,
        controller: AppController,
        calibrator: CanvasCalibrator,
        hotkeys: GlobalHotkeyManager,
        settings_store: UserSettingsStore | None = None,
    ):
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError as exc:
            raise RuntimeError("PySide6 is not installed.") from exc

        settings_store = settings_store or UserSettingsStore()
        saved_settings = settings_store.load()
        controller.session.hotkeys.update(saved_settings.hotkeys)
        controller.session.draw_mouse_button = saved_settings.draw_mouse_button
        controller.session.draw_speed_profile = saved_settings.draw_speed_profile
        controller.draw_executor.settings = executor_settings_for_profile(saved_settings.draw_speed_profile)
        controller.image_generation_client.settings.api_key = saved_settings.api_key
        controller.image_generation_client.settings.proxy_url = saved_settings.proxy_url
        controller.image_generation_client.settings.model = saved_settings.model
        controller.image_generation_client.settings.base_url = saved_settings.base_url

        class ClickablePreviewLabel(QtWidgets.QLabel):
            clicked = QtCore.Signal()

            def mousePressEvent(self, event) -> None:
                if event.button() == QtCore.Qt.LeftButton:
                    self.clicked.emit()
                super().mousePressEvent(event)

        class SpinnerWidget(QtWidgets.QWidget):
            def __init__(self, parent=None) -> None:
                super().__init__(parent)
                self._angle = 0
                self._timer = QtCore.QTimer(self)
                self._timer.timeout.connect(self._tick)
                self.setFixedSize(64, 64)

            def start(self) -> None:
                if not self._timer.isActive():
                    self._timer.start(24)

            def stop(self) -> None:
                if self._timer.isActive():
                    self._timer.stop()
                self._angle = 0
                self.update()

            def is_spinning(self) -> bool:
                return self._timer.isActive()

            def _tick(self) -> None:
                self._angle = (self._angle + 30) % 360
                self.update()

            def paintEvent(self, _event) -> None:
                painter = QtGui.QPainter(self)
                painter.setRenderHint(QtGui.QPainter.Antialiasing)
                painter.translate(self.width() / 2, self.height() / 2)
                painter.rotate(self._angle)

                dot_count = 12
                radius = 22
                dot_radius = 4
                base_color = QtGui.QColor("#fff5ea")
                for index in range(dot_count):
                    alpha = round(255 * ((index + 1) / dot_count))
                    color = QtGui.QColor(base_color)
                    color.setAlpha(alpha)
                    painter.setBrush(color)
                    painter.setPen(QtCore.Qt.NoPen)
                    painter.drawEllipse(QtCore.QPointF(0, -radius), dot_radius, dot_radius)
                    painter.rotate(360 / dot_count)

        class BusyOverlay(QtWidgets.QWidget):
            def __init__(self, parent=None) -> None:
                super().__init__(parent)
                self.setObjectName("busy_overlay")
                self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
                layout = QtWidgets.QVBoxLayout(self)
                layout.setContentsMargins(24, 24, 24, 24)
                layout.addStretch(1)
                self.panel = QtWidgets.QFrame()
                self.panel.setObjectName("busy_overlay_panel")
                panel_layout = QtWidgets.QVBoxLayout(self.panel)
                panel_layout.setContentsMargins(28, 24, 28, 24)
                panel_layout.setSpacing(12)
                panel_layout.setAlignment(QtCore.Qt.AlignCenter)
                self.spinner = SpinnerWidget(self.panel)
                self.message_label = QtWidgets.QLabel("")
                self.message_label.setObjectName("busy_overlay_message")
                self.message_label.setAlignment(QtCore.Qt.AlignCenter)
                panel_layout.addWidget(self.spinner, 0, QtCore.Qt.AlignCenter)
                panel_layout.addWidget(self.message_label, 0, QtCore.Qt.AlignCenter)
                layout.addWidget(self.panel, 0, QtCore.Qt.AlignCenter)
                layout.addStretch(1)
                self.hide()

            def show_message(self, message: str) -> None:
                self.message_label.setText(message)
                self.spinner.start()
                self.show()
                self.raise_()

            def hide_overlay(self) -> None:
                self.spinner.stop()
                self.hide()

        class MainWindow(QtWidgets.QMainWindow):
            hotkey_action_requested = QtCore.Signal(str)
            execution_status_changed = QtCore.Signal(str)
            execution_error = QtCore.Signal(str)
            line_art_generation_busy = QtCore.Signal(bool)
            line_art_generation_succeeded = QtCore.Signal(object)
            line_art_generation_failed = QtCore.Signal(str)
            preview_preparation_busy = QtCore.Signal(bool)
            preview_preparation_succeeded = QtCore.Signal(object)
            preview_preparation_failed = QtCore.Signal(str)

            def __init__(self) -> None:
                super().__init__()
                self.setWindowTitle("STS 绘图助手")
                self.resize(1120, 700)
                self._controller = controller
                self._hotkeys_manager = hotkeys
                self._settings_store = settings_store
                self._original_pixmap: QtGui.QPixmap | None = None
                self._line_art_pixmap: QtGui.QPixmap | None = None
                self._recording_hotkey_name: str | None = None
                self._draw_thread: threading.Thread | None = None
                self._line_art_thread: threading.Thread | None = None
                self._preview_thread: threading.Thread | None = None
                self._preview_restore_state: dict[str, object] | None = None
                self._line_art_busy = False
                self._preview_busy = False
                self._has_centered_on_first_show = False
                self._hotkey_titles = {
                    "calibrate": "定位预览",
                    "start": "开始绘制",
                    "stop": "停止绘制",
                }

                self._hotkey_titles["pause"] = "暂停/继续"

                central = QtWidgets.QWidget()
                self.root_layout = QtWidgets.QVBoxLayout(central)
                self.root_layout.setObjectName("root_layout")
                self.root_layout.setContentsMargins(18, 18, 18, 18)
                self.root_layout.setSpacing(14)

                self._apply_styles()
                self.hotkey_action_requested.connect(self._handle_hotkey_action)
                self.execution_status_changed.connect(self._on_execution_status_changed)
                self.execution_error.connect(self._on_execution_error)
                self.line_art_generation_busy.connect(self._on_line_art_generation_busy)
                self.line_art_generation_succeeded.connect(self._on_line_art_generation_succeeded)
                self.line_art_generation_failed.connect(self._on_line_art_generation_failed)
                self.preview_preparation_busy.connect(self._on_preview_preparation_busy)
                self.preview_preparation_succeeded.connect(self._on_preview_preparation_succeeded)
                self.preview_preparation_failed.connect(self._on_preview_preparation_failed)

                self.preview_panel = self._create_card(
                    "preview_panel",
                    "图片预览",
                    "上方直接对比原图与线稿效果。",
                )
                preview_layout = QtWidgets.QHBoxLayout()
                preview_layout.setSpacing(14)
                self.preview_panel.layout().addLayout(preview_layout)

                original_card, self.original_preview_label = self._create_preview_card(
                    "原图预览",
                    "original_preview",
                    "点击选择图片 / Ctrl+V 粘贴图片",
                    clickable=True,
                )
                line_art_card, self.line_art_preview_label = self._create_preview_card(
                    "线稿预览",
                    "line_art_preview",
                    "生成线稿后会显示在这里",
                )
                preview_layout.addWidget(original_card, 1)
                preview_layout.addWidget(line_art_card, 1)

                self.control_panel = self._create_card(
                    "control_panel",
                    "控制面板",
                    "下方完成配置、生成、校准和绘制。",
                )
                control_layout = QtWidgets.QHBoxLayout()
                control_layout.setSpacing(14)
                self.control_panel.layout().addLayout(control_layout)

                config_card = self._create_card(
                    "config_panel",
                    "模型配置",
                    "导入图片后即可生成线稿。",
                )
                self.image_name_value_label = QtWidgets.QLabel("未选择图片")
                self.image_name_value_label.setObjectName("image_name_value")
                self.api_key_input = QtWidgets.QLineEdit()
                self.api_key_input.setPlaceholderText("输入 OpenRouter 或兼容服务的 API Key")
                self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
                self.api_key_input.setText(controller.image_generation_client.settings.api_key or "")
                self.model_input = QtWidgets.QLineEdit()
                self.model_input.setPlaceholderText("输入模型名称")
                self.model_input.setText(controller.image_generation_client.settings.model)
                self.base_url_input = QtWidgets.QLineEdit()
                self.base_url_input.setPlaceholderText("输入接口地址")
                self.base_url_input.setText(controller.image_generation_client.settings.base_url)
                self.proxy_input = QtWidgets.QLineEdit()
                self.proxy_input.setPlaceholderText("输入 HTTP(S) 代理地址")
                self.proxy_input.setText(controller.image_generation_client.settings.proxy_url or "")
                self.fill_proxy_button = QtWidgets.QPushButton("填入 7890")
                self.fill_proxy_button.setObjectName("compact_button")
                self.hotkey_value_labels: dict[str, QtWidgets.QLabel] = {}
                self.hotkey_record_buttons: dict[str, QtWidgets.QPushButton] = {}
                self.hotkey_status_labels: dict[str, QtWidgets.QLabel] = {}
                self.mouse_button_combo = QtWidgets.QComboBox()
                self.mouse_button_combo.addItem("左键", "left")
                self.mouse_button_combo.addItem("右键", "right")
                self.mouse_button_combo.setCurrentIndex(
                    0 if controller.session.draw_mouse_button == "left" else 1
                )
                self.speed_profile_combo = QtWidgets.QComboBox()
                self.speed_profile_combo.addItem("稳定", "stable")
                self.speed_profile_combo.addItem("均衡", "balanced")
                self.speed_profile_combo.addItem("快速", "fast")
                speed_profile_index = self.speed_profile_combo.findData(controller.session.draw_speed_profile)
                self.speed_profile_combo.setCurrentIndex(max(speed_profile_index, 0))
                self._paste_aware_inputs = (
                    self.api_key_input,
                    self.model_input,
                    self.base_url_input,
                    self.proxy_input,
                )
                for input_widget in self._paste_aware_inputs:
                    input_widget.installEventFilter(self)
                form_layout = QtWidgets.QFormLayout()
                form_layout.setLabelAlignment(QtCore.Qt.AlignLeft)
                form_layout.setFormAlignment(QtCore.Qt.AlignTop)
                form_layout.setHorizontalSpacing(10)
                form_layout.setVerticalSpacing(10)
                form_layout.addRow("当前图片", self.image_name_value_label)
                form_layout.addRow("API Key", self.api_key_input)
                form_layout.addRow("模型", self.model_input)
                form_layout.addRow("接口地址", self.base_url_input)
                proxy_row = QtWidgets.QWidget()
                proxy_row_layout = QtWidgets.QHBoxLayout(proxy_row)
                proxy_row_layout.setContentsMargins(0, 0, 0, 0)
                proxy_row_layout.setSpacing(8)
                proxy_row_layout.addWidget(self.proxy_input, 1)
                proxy_row_layout.addWidget(self.fill_proxy_button)
                form_layout.addRow("代理地址", proxy_row)
                config_card.layout().addLayout(form_layout)
                config_card.layout().addStretch(1)

                self.shortcut_card = self._create_card(
                    "shortcut_panel",
                    "快捷与绘制",
                    "在这里调整全局热键和绘制按键。",
                )
                hotkey_form = QtWidgets.QFormLayout()
                hotkey_form.setLabelAlignment(QtCore.Qt.AlignLeft)
                hotkey_form.setFormAlignment(QtCore.Qt.AlignTop)
                hotkey_form.setHorizontalSpacing(8)
                hotkey_form.setVerticalSpacing(8)
                for action in ("calibrate", "start", "pause", "stop"):
                    value_label = QtWidgets.QLabel()
                    value_label.setObjectName("hotkey_value")
                    status_label = QtWidgets.QLabel()
                    status_label.setObjectName("hotkey_status")
                    button = QtWidgets.QPushButton("设置")
                    button.setObjectName("compact_button")
                    button.clicked.connect(
                        lambda _checked=False, name=action: self._begin_hotkey_recording(name)
                    )
                    row_widget = QtWidgets.QWidget()
                    row_layout = QtWidgets.QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(8)
                    row_layout.addWidget(value_label, 2)
                    row_layout.addWidget(status_label, 1)
                    row_layout.addWidget(button)
                    hotkey_form.addRow(self._hotkey_titles[action], row_widget)
                    self.hotkey_value_labels[action] = value_label
                    self.hotkey_record_buttons[action] = button
                    self.hotkey_status_labels[action] = status_label
                hotkey_form.addRow("绘画按键", self.mouse_button_combo)
                hotkey_form.addRow("绘画速度", self.speed_profile_combo)
                self.shortcut_card.layout().addLayout(hotkey_form)
                self.shortcut_card.layout().addStretch(1)

                actions_card = self._create_card(
                    "actions_panel",
                    "操作",
                    "生成线稿后定位预览，再开始绘制。",
                )
                actions_grid = QtWidgets.QGridLayout()
                actions_grid.setHorizontalSpacing(8)
                actions_grid.setVerticalSpacing(8)
                self.line_art_button = QtWidgets.QPushButton("生成线稿")
                self.preview_button = QtWidgets.QPushButton("定位预览")
                self.start_button = QtWidgets.QPushButton("开始绘制")
                self.start_button.setObjectName("primary_button")
                self.stop_button = QtWidgets.QPushButton("停止绘制")
                actions_grid.addWidget(self.line_art_button, 0, 0)
                actions_grid.addWidget(self.preview_button, 0, 1)
                actions_grid.addWidget(self.start_button, 1, 0)
                actions_grid.addWidget(self.stop_button, 1, 1)
                actions_card.layout().addLayout(actions_grid)
                hint_label = QtWidgets.QLabel(
                    "点击原图预览可选择图片，Ctrl+V 可直接粘贴；定位预览时滚轮缩放。"
                )
                hint_label.setObjectName("hint_label")
                hint_label.setWordWrap(True)
                actions_card.layout().addWidget(hint_label)
                actions_card.layout().addStretch(1)

                status_card = self._create_card(
                    "status_panel",
                    "状态",
                    "查看当前导入、线稿和预览状态。",
                )
                status_layout = QtWidgets.QFormLayout()
                status_layout.setLabelAlignment(QtCore.Qt.AlignLeft)
                status_layout.setFormAlignment(QtCore.Qt.AlignTop)
                status_layout.setHorizontalSpacing(10)
                status_layout.setVerticalSpacing(10)
                self.status_value_label = QtWidgets.QLabel("待开始")
                self.status_value_label.setWordWrap(True)
                self.status_value_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
                self.region_value_label = QtWidgets.QLabel("未选择")
                self.preview_value_label = QtWidgets.QLabel("未生成")
                status_layout.addRow("任务状态", self.status_value_label)
                status_layout.addRow("绘制区域", self.region_value_label)
                status_layout.addRow("预览结果", self.preview_value_label)
                status_card.layout().addLayout(status_layout)
                status_card.layout().addStretch(1)

                control_layout.addWidget(config_card, 4)
                control_layout.addWidget(self.shortcut_card, 4)
                control_layout.addWidget(actions_card, 3)
                control_layout.addWidget(status_card, 3)

                self.root_layout.addWidget(self.preview_panel, 7)
                self.root_layout.addWidget(self.control_panel, 4)
                self.setCentralWidget(central)
                self.busy_overlay = BusyOverlay(central)
                self.busy_spinner = self.busy_overlay.spinner
                self.busy_message_label = self.busy_overlay.message_label
                self._update_busy_overlay_geometry()

                self.original_preview_label.clicked.connect(self._browse_image)
                self.line_art_button.clicked.connect(self._generate_line_art)
                self.preview_button.clicked.connect(self._preview)
                self.start_button.clicked.connect(self._start)
                self.stop_button.clicked.connect(self._request_stop)
                self.fill_proxy_button.clicked.connect(self._fill_local_proxy)
                self.api_key_input.textChanged.connect(self._on_runtime_text_changed)
                self.model_input.textChanged.connect(self._on_runtime_text_changed)
                self.base_url_input.textChanged.connect(self._on_runtime_text_changed)
                self.proxy_input.textChanged.connect(self._on_runtime_text_changed)
                self.mouse_button_combo.currentIndexChanged.connect(self._on_draw_mouse_button_changed)
                self.speed_profile_combo.currentIndexChanged.connect(self._on_draw_speed_profile_changed)
                self._refresh_hotkey_labels()

                self.paste_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+V"), self)
                self.paste_shortcut.setContext(QtCore.Qt.ApplicationShortcut)
                self.paste_shortcut.activated.connect(self._paste_image_from_clipboard)

                self._register_hotkeys()
                self._sync_runtime_control_states()

            def resizeEvent(self, event) -> None:
                super().resizeEvent(event)
                self._update_busy_overlay_geometry()
                self._refresh_preview_pixmaps()

            def showEvent(self, event) -> None:
                super().showEvent(event)
                self._center_on_first_show()

            def _update_busy_overlay_geometry(self) -> None:
                if self.centralWidget() is not None and hasattr(self, "busy_overlay"):
                    self.busy_overlay.setGeometry(self.centralWidget().rect())

            def _center_on_first_show(self) -> None:
                if self._has_centered_on_first_show:
                    return
                screen = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
                if screen is None:
                    screen = QtGui.QGuiApplication.primaryScreen()
                if screen is None:
                    return
                available = screen.availableGeometry()
                x = available.x() + max((available.width() - self.width()) // 2, 0)
                y = available.y() + max((available.height() - self.height()) // 2, 0)
                self.move(x, y)
                self._has_centered_on_first_show = True

            def keyPressEvent(self, event) -> None:
                if self._recording_hotkey_name is not None:
                    if event.key() == QtCore.Qt.Key_Escape:
                        self._finish_hotkey_recording("已取消热键修改")
                        event.accept()
                        return
                    hotkey = self._hotkey_from_event(event)
                    if hotkey is None:
                        self.status_value_label.setText("热键至少需要 Ctrl、Alt 或 Shift 加普通按键")
                        event.accept()
                        return
                    duplicate_hotkeys = {
                        value
                        for name, value in controller.session.hotkeys.items()
                        if name != self._recording_hotkey_name
                    }
                    if hotkey in duplicate_hotkeys:
                        self.status_value_label.setText("该热键已在使用，请换一个组合键")
                        event.accept()
                        return
                    action = self._recording_hotkey_name
                    controller.session.hotkeys[action] = hotkey
                    self._refresh_hotkey_labels()
                    self._save_runtime_settings()
                    self._register_hotkeys()
                    self._finish_hotkey_recording(self._hotkey_update_status(action))
                    event.accept()
                    return
                super().keyPressEvent(event)

            def eventFilter(self, watched, event) -> bool:
                if (
                    watched in getattr(self, "_paste_aware_inputs", ())
                    and event.type() == QtCore.QEvent.KeyPress
                    and event.matches(QtGui.QKeySequence.Paste)
                ):
                    if self._try_import_image_from_clipboard():
                        event.accept()
                        return True
                return super().eventFilter(watched, event)

            def _begin_hotkey_recording(self, action: str) -> None:
                self._recording_hotkey_name = action
                for name, button in self.hotkey_record_buttons.items():
                    button.setText("按下快捷键" if name == action else "设置")
                self.status_value_label.setText(f"请按下新的{self._hotkey_titles[action]}热键，Esc 取消")
                self.grabKeyboard()

            def _finish_hotkey_recording(self, status_text: str | None = None) -> None:
                if self._recording_hotkey_name is not None:
                    self.releaseKeyboard()
                self._recording_hotkey_name = None
                for button in self.hotkey_record_buttons.values():
                    button.setText("设置")
                if status_text is not None:
                    self.status_value_label.setText(status_text)

            def _hotkey_from_event(self, event) -> str | None:
                modifiers = []
                if event.modifiers() & QtCore.Qt.ControlModifier:
                    modifiers.append("ctrl")
                if event.modifiers() & QtCore.Qt.AltModifier:
                    modifiers.append("alt")
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    modifiers.append("shift")
                if not modifiers:
                    return None

                modifier_keys = {
                    QtCore.Qt.Key_Control,
                    QtCore.Qt.Key_Shift,
                    QtCore.Qt.Key_Alt,
                    QtCore.Qt.Key_Meta,
                }
                if event.key() in modifier_keys:
                    return None

                key_name = self._key_name_from_event(event)
                if key_name is None:
                    return None
                return "+".join(modifiers + [key_name])

            def _key_name_from_event(self, event) -> str | None:
                text = event.text().strip().lower()
                if len(text) == 1 and text.isprintable():
                    return text
                special_keys = {
                    QtCore.Qt.Key_Space: "space",
                    QtCore.Qt.Key_Tab: "tab",
                    QtCore.Qt.Key_Return: "enter",
                    QtCore.Qt.Key_Enter: "enter",
                    QtCore.Qt.Key_Delete: "delete",
                    QtCore.Qt.Key_Backspace: "backspace",
                    QtCore.Qt.Key_Left: "left",
                    QtCore.Qt.Key_Right: "right",
                    QtCore.Qt.Key_Up: "up",
                    QtCore.Qt.Key_Down: "down",
                }
                if event.key() in special_keys:
                    return special_keys[event.key()]
                if QtCore.Qt.Key_F1 <= event.key() <= QtCore.Qt.Key_F12:
                    return f"f{event.key() - QtCore.Qt.Key_F1 + 1}"
                return None

            def _format_hotkey_display(self, hotkey: str) -> str:
                display_parts = []
                for part in hotkey.split("+"):
                    if len(part) == 1:
                        display_parts.append(part.upper())
                    else:
                        display_parts.append(part.capitalize())
                return "+".join(display_parts)

            def _refresh_hotkey_labels(self) -> None:
                for action, label in self.hotkey_value_labels.items():
                    label.setText(self._format_hotkey_display(controller.session.hotkeys[action]))
                    self._render_hotkey_status(action)

            def _register_hotkeys(self) -> None:
                results = self._hotkeys_manager.register(
                    {
                        controller.session.hotkeys["calibrate"]: lambda: self._queue_hotkey_action("calibrate"),
                        controller.session.hotkeys["start"]: lambda: self._queue_hotkey_action("start"),
                        controller.session.hotkeys["stop"]: self._stop_from_hotkey,
                        controller.session.hotkeys["pause"]: self._toggle_pause_from_hotkey,
                    }
                )
                for action, hotkey in controller.session.hotkeys.items():
                    result = results.get(hotkey) if isinstance(results, dict) else None
                    if result is None:
                        result = HotkeyCheckResult(hotkey=hotkey, ok=True)
                    self._set_hotkey_status(action, result)
                self._refresh_hotkey_labels()

            def _queue_hotkey_action(self, action: str) -> None:
                self.hotkey_action_requested.emit(action)

            def _handle_hotkey_action(self, action: str) -> None:
                if action == "calibrate":
                    self._preview()
                    return
                if action == "start":
                    self._start()
                    return
                if action == "stop":
                    self._request_stop()

            def _stop_from_hotkey(self) -> None:
                self._request_stop()

            def _toggle_pause_from_hotkey(self) -> None:
                controller.toggle_pause()

            def _request_stop(self) -> None:
                controller.cancel()
                self.execution_status_changed.emit(controller.session.status)

            def _toggle_pause(self) -> None:
                controller.toggle_pause()

            def _run_drawing(self) -> None:
                try:
                    controller.start_drawing(status_callback=self.execution_status_changed.emit)
                    self.execution_status_changed.emit(controller.session.status)
                except Exception as exc:  # pragma: no cover
                    self.execution_error.emit(self._format_error(exc))

            def _run_line_art_generation(self) -> None:
                self.line_art_generation_busy.emit(True)
                try:
                    self._sync_runtime_inputs_to_controller()
                    controller.generate_line_art()
                except Exception as exc:  # pragma: no cover
                    self.line_art_generation_failed.emit(self._format_error(exc))
                    return
                self.line_art_generation_succeeded.emit(controller.session.line_art)

            def _set_line_art_generation_controls_enabled(self, enabled: bool) -> None:
                self._line_art_busy = not enabled
                self._sync_runtime_control_states()

            def _run_preview_preparation(self) -> None:
                self.preview_preparation_busy.emit(True)
                try:
                    preview = controller.prepare_preview()
                except Exception as exc:  # pragma: no cover
                    self.preview_preparation_failed.emit(self._format_error(exc))
                    return
                self.preview_preparation_succeeded.emit(preview)

            def _set_preview_preparation_controls_enabled(self, enabled: bool) -> None:
                self._preview_busy = not enabled
                self._sync_runtime_control_states()

            def _sync_runtime_control_states(self) -> None:
                drawing_active = controller.session.status in {"countdown", "running", "paused"}
                busy = self._line_art_busy or self._preview_busy
                runtime_controls_enabled = not busy and not drawing_active

                self.line_art_button.setEnabled(runtime_controls_enabled)
                self.preview_button.setEnabled(runtime_controls_enabled)
                self.original_preview_label.setEnabled(runtime_controls_enabled)
                self.api_key_input.setEnabled(runtime_controls_enabled)
                self.model_input.setEnabled(runtime_controls_enabled)
                self.base_url_input.setEnabled(runtime_controls_enabled)
                self.proxy_input.setEnabled(runtime_controls_enabled)
                self.fill_proxy_button.setEnabled(runtime_controls_enabled)
                self.mouse_button_combo.setEnabled(runtime_controls_enabled)
                self.speed_profile_combo.setEnabled(runtime_controls_enabled)
                for button in self.hotkey_record_buttons.values():
                    button.setEnabled(runtime_controls_enabled)

                if busy:
                    self.start_button.setEnabled(False)
                else:
                    self.start_button.setEnabled(True)
                self.stop_button.setEnabled(drawing_active)
                self._update_start_button()

            def _update_start_button(self) -> None:
                if controller.session.status == "paused":
                    self.start_button.setText("继续绘制")
                    return
                if controller.session.status in {"countdown", "running"}:
                    self.start_button.setText("暂停绘制")
                    return
                self.start_button.setText("开始绘制")

            def _on_line_art_generation_busy(self, is_busy: bool) -> None:
                self._set_line_art_generation_controls_enabled(not is_busy)
                if is_busy:
                    self.busy_overlay.show_message("正在生成线稿...")
                    self.status_value_label.setText("正在生成线稿...")
                else:
                    self.busy_overlay.hide_overlay()
                self._sync_runtime_control_states()

            def _on_line_art_generation_succeeded(self, line_art) -> None:
                self._line_art_thread = None
                self._set_line_art_generation_controls_enabled(True)
                self.busy_overlay.hide_overlay()
                if line_art is not None:
                    self._set_line_art_preview(line_art)
                controller.session.active_region = None
                controller.session.stroke_plan = None
                controller.session.last_preview = None
                self.status_value_label.setText("线稿已生成")
                self.region_value_label.setText("未选择")
                self.preview_value_label.setText("待生成")

                self._sync_runtime_control_states()

            def _on_line_art_generation_failed(self, message: str) -> None:
                self._line_art_thread = None
                self._set_line_art_generation_controls_enabled(True)
                self.busy_overlay.hide_overlay()
                self.status_value_label.setText(message)
                self._sync_runtime_control_states()

            def _on_preview_preparation_busy(self, is_busy: bool) -> None:
                self._set_preview_preparation_controls_enabled(not is_busy)
                if is_busy:
                    self.busy_overlay.show_message("正在生成预览...")
                    self.status_value_label.setText("正在生成预览...")
                else:
                    self.busy_overlay.hide_overlay()

                self._sync_runtime_control_states()

            def _on_preview_preparation_succeeded(self, preview: object) -> None:
                self._preview_thread = None
                self._preview_restore_state = None
                self._set_preview_preparation_controls_enabled(True)
                self.busy_overlay.hide_overlay()
                segment_count = getattr(preview, "segment_count", None)
                if segment_count is None and isinstance(preview, dict):
                    segment_count = preview.get("segment_count")
                self.status_value_label.setText("预览定位已确认")
                self.preview_value_label.setText(f"{segment_count or 0} 段路径")

                self._sync_runtime_control_states()

            def _on_preview_preparation_failed(self, message: str) -> None:
                if self._preview_restore_state is not None:
                    controller.session.active_region = self._preview_restore_state["active_region"]
                    controller.session.preview_scale = self._preview_restore_state["preview_scale"]
                    controller.session.stroke_plan = self._preview_restore_state["stroke_plan"]
                    controller.session.last_preview = self._preview_restore_state["last_preview"]
                    self.region_value_label.setText(self._preview_restore_state["region_label"])
                    self.preview_value_label.setText(self._preview_restore_state["preview_label"])
                self._preview_restore_state = None
                self._preview_thread = None
                self._set_preview_preparation_controls_enabled(True)
                self.busy_overlay.hide_overlay()
                self.status_value_label.setText(message)

                self._sync_runtime_control_states()

            def _on_execution_status_changed(self, status: str) -> None:
                controller.session.status = status
                status_map = {
                    "countdown": "倒计时中",
                    "running": "正在绘制",
                    "cancelled": "已停止",
                    "completed": "绘制完成",
                }
                status_map["paused"] = "已暂停绘制"
                self.status_value_label.setText(status_map.get(status, status))
                if status in {"cancelled", "completed"}:
                    self._draw_thread = None
                self._sync_runtime_control_states()

            def _on_execution_error(self, message: str) -> None:
                self.status_value_label.setText(message)
                self._draw_thread = None
                self._sync_runtime_control_states()

            def _hotkey_update_status(self, action: str) -> str:
                status = controller.session.hotkey_statuses[action]
                if status.is_active:
                    text = f"已更新{self._hotkey_titles[action]}热键"
                else:
                    text = f"已保存{self._hotkey_titles[action]}热键，但当前未生效：{status.message}"
                hotkey_parts = set(status.hotkey.split("+"))
                if status.is_active and {"alt", "shift"}.issubset(hotkey_parts):
                    text += "；注意 Alt+Shift 可能与系统输入法或键盘布局切换冲突"
                return text

            def _set_hotkey_status(self, action: str, result: HotkeyCheckResult) -> None:
                controller.session.hotkey_statuses[action] = HotkeyStatus(
                    hotkey=result.hotkey,
                    is_active=result.ok,
                    conflict_reason=result.conflict_reason,
                    message="正常" if result.ok else f"未生效：{result.message}",
                )

            def _render_hotkey_status(self, action: str) -> None:
                status = controller.session.hotkey_statuses[action]
                label = self.hotkey_status_labels[action]
                label.setText(status.message)
                label.setProperty("active", status.is_active)
                self.style().unpolish(label)
                self.style().polish(label)
                label.update()

            def _save_runtime_settings(self) -> None:
                self._settings_store.save(self._current_user_settings())

            def _current_user_settings(self) -> UserSettings:
                return UserSettings(
                    hotkeys=dict(controller.session.hotkeys),
                    draw_mouse_button=controller.session.draw_mouse_button,
                    draw_speed_profile=controller.session.draw_speed_profile,
                    api_key=self.api_key_input.text().strip(),
                    proxy_url=self.proxy_input.text().strip() or None,
                    model=self.model_input.text().strip(),
                    base_url=self.base_url_input.text().strip(),
                )

            def _sync_runtime_inputs_to_controller(self) -> None:
                controller.image_generation_client.settings.api_key = self.api_key_input.text().strip()
                controller.image_generation_client.settings.model = self.model_input.text().strip()
                controller.image_generation_client.settings.base_url = self.base_url_input.text().strip()
                controller.image_generation_client.settings.proxy_url = self.proxy_input.text().strip() or None

            def _on_runtime_text_changed(self, _value: str) -> None:
                self._sync_runtime_inputs_to_controller()
                self._save_runtime_settings()

            def _on_draw_mouse_button_changed(self, _index=None) -> None:
                controller.session.draw_mouse_button = self.mouse_button_combo.currentData()
                self._save_runtime_settings()

            def _on_draw_speed_profile_changed(self, _index=None) -> None:
                controller.session.draw_speed_profile = self.speed_profile_combo.currentData()
                controller.draw_executor.settings = executor_settings_for_profile(
                    controller.session.draw_speed_profile
                )
                self._save_runtime_settings()

            def _fill_local_proxy(self) -> None:
                self.proxy_input.setText("http://127.0.0.1:7890")

            def _browse_image(self) -> None:
                image_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, "选择图片", "", "Images (*.png *.jpg *.jpeg *.webp)"
                )
                if image_path:
                    self._load_image_from_path(image_path)

            def _paste_image_from_clipboard(self) -> None:
                if not self._try_import_image_from_clipboard():
                    self.status_value_label.setText("剪贴板中没有可用图片")

            def _try_import_image_from_clipboard(self) -> bool:
                image_path = self._clipboard_to_temp_image()
                if not image_path:
                    return False
                self._load_image_from_path(image_path, from_clipboard=True)
                return True

            def _clipboard_to_temp_image(self) -> str | None:
                clipboard = QtWidgets.QApplication.clipboard()
                mime_data = clipboard.mimeData()

                if mime_data is not None and mime_data.hasUrls():
                    for url in mime_data.urls():
                        local_path = self._local_image_path_from_url(url)
                        if local_path is not None:
                            return local_path

                text = clipboard.text().strip()
                if text:
                    local_path = self._local_image_path_from_text(text)
                    if local_path is not None:
                        return local_path

                image = clipboard.image()
                if image.isNull():
                    return None

                temp_dir = Path(tempfile.gettempdir()) / "sts_draw_clipboard"
                temp_dir.mkdir(parents=True, exist_ok=True)
                image_path = temp_dir / f"clipboard_{uuid.uuid4().hex}.png"
                if not image.save(str(image_path), "PNG"):
                    return None
                return str(image_path)

            def _local_image_path_from_url(self, url) -> str | None:
                if not url.isLocalFile():
                    return None
                return self._validated_image_path(url.toLocalFile())

            def _local_image_path_from_text(self, text: str) -> str | None:
                url = QtCore.QUrl(text)
                if url.isValid() and url.isLocalFile():
                    return self._validated_image_path(url.toLocalFile())
                return self._validated_image_path(text)

            def _validated_image_path(self, candidate: str) -> str | None:
                path = Path(candidate)
                if not path.exists() or not path.is_file():
                    return None
                if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    return None
                return str(path)

            def _load_image_from_path(self, image_path: str, from_clipboard: bool = False) -> None:
                controller.load_image(image_path)
                controller.session.line_art = None
                controller.session.active_region = None
                controller.session.stroke_plan = None
                controller.session.last_preview = None
                controller.session.preview_scale = None
                self.image_name_value_label.setText(Path(image_path).name)
                self._set_original_preview(image_path)
                self._clear_line_art_preview()
                self.preview_value_label.setText("未生成")
                self.region_value_label.setText("未选择")
                self.status_value_label.setText("已从剪贴板导入图片" if from_clipboard else "已载入图片")

            def _generate_line_art(self) -> None:
                if self._line_art_thread is not None and self._line_art_thread.is_alive():
                    self.status_value_label.setText("正在生成线稿...")
                    return
                if not controller.session.image_path:
                    self.status_value_label.setText(self._format_error(RuntimeError("No image has been selected.")))
                    return
                self._on_line_art_generation_busy(True)
                self._line_art_thread = threading.Thread(target=self._run_line_art_generation, daemon=True)
                self._line_art_thread.start()

            def _preview(self) -> None:
                if controller.session.line_art is None:
                    self.status_value_label.setText("请先生成线稿")
                    return

                try:
                    placement = calibrator.place_preview(
                        controller.session.line_art,
                        initial_scale=controller.session.preview_scale or 1.0,
                    )
                except Exception as exc:  # pragma: no cover
                    self.status_value_label.setText(self._format_error(exc))
                    return

                controller.set_region(placement.region)
                controller.session.preview_scale = placement.scale
                self.region_value_label.setText(f"{placement.region.width} × {placement.region.height}")

                try:
                    preview = controller.prepare_preview()
                except Exception as exc:  # pragma: no cover
                    self.status_value_label.setText(self._format_error(exc))
                    return
                segment_count = getattr(preview, "segment_count", None)
                if segment_count is None and isinstance(preview, dict):
                    segment_count = preview.get("segment_count")
                self.status_value_label.setText("预览定位已确认")
                self.preview_value_label.setText(f"{segment_count or 0} 段路径")

            def _preview(self) -> None:
                if controller.session.line_art is None:
                    self.status_value_label.setText("请先生成线稿")
                    return
                if self._preview_thread is not None and self._preview_thread.is_alive():
                    self.status_value_label.setText("正在生成预览...")
                    return

                try:
                    placement = calibrator.place_preview(
                        controller.session.line_art,
                        initial_scale=controller.session.preview_scale or 1.0,
                    )
                except Exception as exc:  # pragma: no cover
                    self.status_value_label.setText(self._format_error(exc))
                    return

                self._preview_restore_state = {
                    "active_region": controller.session.active_region,
                    "preview_scale": controller.session.preview_scale,
                    "stroke_plan": controller.session.stroke_plan,
                    "last_preview": controller.session.last_preview,
                    "region_label": self.region_value_label.text(),
                    "preview_label": self.preview_value_label.text(),
                }
                controller.set_region(placement.region)
                controller.session.preview_scale = placement.scale
                self.region_value_label.setText(f"{placement.region.width} × {placement.region.height}")
                self._on_preview_preparation_busy(True)
                self._preview_thread = threading.Thread(target=self._run_preview_preparation, daemon=True)
                self._preview_thread.start()

            def _start(self) -> None:
                if controller.session.status in {"countdown", "running", "paused"}:
                    self._toggle_pause()
                    return
                if self._draw_thread is not None and self._draw_thread.is_alive():
                    self.status_value_label.setText("正在绘制")
                    return
                self._draw_thread = threading.Thread(target=self._run_drawing, daemon=True)
                self._draw_thread.start()
                self.status_value_label.setText("正在绘制")

                self._sync_runtime_control_states()

            def closeEvent(self, event) -> None:
                event.accept()

            def _update_start_button(self) -> None:
                if controller.session.status == "paused":
                    self.start_button.setText("继续绘制")
                    return
                if controller.session.status in {"countdown", "running"}:
                    self.start_button.setText("暂停绘制")
                    return
                self.start_button.setText("开始绘制")

            def _create_card(self, object_name: str, title: str, subtitle: str):
                card = QtWidgets.QFrame()
                card.setObjectName(object_name)
                card_layout = QtWidgets.QVBoxLayout(card)
                card_layout.setContentsMargins(14, 14, 14, 14)
                card_layout.setSpacing(8)

                title_label = QtWidgets.QLabel(title)
                title_label.setObjectName("card_title")
                subtitle_label = QtWidgets.QLabel(subtitle)
                subtitle_label.setObjectName("card_subtitle")
                subtitle_label.setWordWrap(True)

                card_layout.addWidget(title_label)
                card_layout.addWidget(subtitle_label)
                return card

            def _create_preview_card(
                self,
                title: str,
                preview_object_name: str,
                empty_text: str,
                clickable: bool = False,
            ):
                card = QtWidgets.QFrame()
                card.setObjectName("preview_card")
                card_layout = QtWidgets.QVBoxLayout(card)
                card_layout.setContentsMargins(12, 12, 12, 12)
                card_layout.setSpacing(8)

                title_label = QtWidgets.QLabel(title)
                title_label.setObjectName("preview_title")
                preview_label = ClickablePreviewLabel() if clickable else QtWidgets.QLabel()
                preview_label.setObjectName(preview_object_name)
                preview_label.setAlignment(QtCore.Qt.AlignCenter)
                preview_label.setWordWrap(True)
                preview_label.setMinimumHeight(220)
                preview_label.setText(empty_text)

                card_layout.addWidget(title_label)
                card_layout.addWidget(preview_label, 1)
                return card, preview_label

            def _set_original_preview(self, image_path: str) -> None:
                pixmap = QtGui.QPixmap(image_path)
                if pixmap.isNull():
                    self.original_preview_label.setText("无法预览原图")
                    self._original_pixmap = None
                    return
                self._original_pixmap = pixmap
                self._update_preview_label(self.original_preview_label, pixmap)

            def _set_line_art_preview(self, line_art) -> None:
                pixmap = QtGui.QPixmap()
                pixmap.loadFromData(line_art.image_bytes)
                if pixmap.isNull():
                    self.line_art_preview_label.setText("无法预览线稿")
                    self._line_art_pixmap = None
                    return
                self._line_art_pixmap = pixmap
                self._update_preview_label(self.line_art_preview_label, pixmap)

            def _clear_line_art_preview(self) -> None:
                self._line_art_pixmap = None
                self.line_art_preview_label.clear()
                self.line_art_preview_label.setText("生成线稿后会显示在这里")

            def _refresh_preview_pixmaps(self) -> None:
                if self._original_pixmap is not None:
                    self._update_preview_label(self.original_preview_label, self._original_pixmap)
                if self._line_art_pixmap is not None:
                    self._update_preview_label(self.line_art_preview_label, self._line_art_pixmap)

            def _update_preview_label(self, label, pixmap) -> None:
                scaled = pixmap.scaled(
                    label.size(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
                label.setPixmap(scaled)

            def _format_error(self, exc: Exception) -> str:
                text = str(exc)
                mapping = {
                    "No image has been selected.": "请先选择图片",
                    "Line art is not ready.": "请先生成线稿",
                    "Calibration region is required.": "请先选择绘制区域",
                    "Preview must be prepared first.": "请先生成预览",
                    "Preview placement was cancelled.": "已取消定位预览",
                }
                return mapping.get(text, f"操作失败：{text}")

            def _apply_styles(self) -> None:
                self.setStyleSheet(
                    """
                    QMainWindow { background: #f5f1ea; }
                    QFrame#preview_panel, QFrame#control_panel, QFrame#config_panel,
                    QFrame#actions_panel, QFrame#status_panel, QFrame#preview_card,
                    QFrame#shortcut_panel {
                        background: #fffdf9;
                        border: 1px solid #e6dfd4;
                        border-radius: 18px;
                    }
                    QLabel { color: #3f342b; font-size: 14px; }
                    QLabel#card_title {
                        font-size: 20px;
                        font-weight: 700;
                        color: #2f251e;
                    }
                    QLabel#card_subtitle, QLabel#hint_label {
                        color: #7a6a5f;
                    }
                    QLabel#preview_title {
                        font-size: 13px;
                        font-weight: 600;
                        color: #6b594d;
                    }
                    QLabel#image_name_value,
                    QLabel#hotkey_value {
                        min-height: 38px;
                        padding: 8px 12px;
                        background: #f6efe5;
                        border: 1px solid #e5d9c9;
                        border-radius: 12px;
                    }
                    QLabel#hotkey_status {
                        min-height: 28px;
                        padding: 4px 10px;
                        border-radius: 10px;
                        font-size: 12px;
                        font-weight: 600;
                        background: #eef5ee;
                        color: #2f7d4f;
                    }
                    QLabel#hotkey_status[active="false"] {
                        background: #fdecec;
                        color: #b13c3c;
                    }
                    QLabel#original_preview, QLabel#line_art_preview {
                        background: #faf6f0;
                        border: 1px dashed #d8c9b7;
                        border-radius: 14px;
                        color: #8a796d;
                        padding: 12px;
                    }
                    QLabel#original_preview:hover {
                        border-color: #d98652;
                        background: #fcf2e6;
                    }
                    QLineEdit {
                        min-height: 38px;
                        padding: 0 12px;
                        background: #fffaf4;
                        border: 1px solid #dccfbe;
                        border-radius: 12px;
                        selection-background-color: #df9362;
                    }
                    QComboBox {
                        min-height: 38px;
                        padding: 0 12px;
                        background: #fffaf4;
                        border: 1px solid #dccfbe;
                        border-radius: 12px;
                    }
                    QPushButton {
                        min-height: 40px;
                        border-radius: 12px;
                        border: 1px solid #d6c8b7;
                        background: #fff9f2;
                        color: #41362d;
                        font-weight: 600;
                        padding: 0 14px;
                    }
                    QPushButton#compact_button {
                        min-height: 38px;
                        padding: 0 12px;
                    }
                    QPushButton:hover { background: #fbf0e3; }
                    QPushButton#primary_button {
                        background: #d98652;
                        color: white;
                        border: none;
                    }
                    QPushButton#primary_button:hover { background: #c97744; }
                    QWidget#busy_overlay {
                        background: rgba(28, 24, 21, 150);
                    }
                    QFrame#busy_overlay_panel {
                        background: rgba(43, 36, 31, 230);
                        border: 1px solid rgba(255, 220, 191, 90);
                        border-radius: 20px;
                    }
                    QLabel#busy_overlay_message {
                        color: #fff8ef;
                        font-size: 15px;
                        font-weight: 600;
                    }
                    """
                )

        return MainWindow()


def build_default_window():
    from sts_draw.draw_executor import DrawExecutor
    from sts_draw.image_generation_client import OpenAICompatibleClient
    from sts_draw.preview_renderer import PreviewRenderer
    from sts_draw.stroke_planner import StrokePlanner

    controller = AppController(
        gemini_client=OpenAICompatibleClient(),
        stroke_planner=StrokePlanner(),
        preview_renderer=PreviewRenderer(),
        draw_executor=DrawExecutor(),
    )
    calibrator = CanvasCalibrator()
    hotkeys = GlobalHotkeyManager()
    settings_store = UserSettingsStore()
    return MainWindowFactory().create(controller, calibrator, hotkeys, settings_store=settings_store)
