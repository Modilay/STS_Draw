from __future__ import annotations

from pathlib import Path

from sts_draw.app_controller import AppController
from sts_draw.canvas_calibrator import CanvasCalibrator
from sts_draw.global_hotkeys import GlobalHotkeyManager


class MainWindowFactory:
    def create(self, controller: AppController, calibrator: CanvasCalibrator, hotkeys: GlobalHotkeyManager):
        try:
            from PySide6 import QtWidgets
        except ImportError as exc:
            raise RuntimeError("PySide6 is not installed.") from exc

        class MainWindow(QtWidgets.QMainWindow):
            def __init__(self) -> None:
                super().__init__()
                self.setWindowTitle("STS Draw")
                self.resize(720, 420)

                central = QtWidgets.QWidget()
                layout = QtWidgets.QVBoxLayout(central)
                self.status_label = QtWidgets.QLabel("Idle")
                self.image_label = QtWidgets.QLabel("No image selected")
                self.api_key_input = QtWidgets.QLineEdit()
                self.api_key_input.setPlaceholderText("Gemini API Key")
                self.api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
                browse_button = QtWidgets.QPushButton("Select image")
                line_art_button = QtWidgets.QPushButton("Generate line art")
                calibrate_button = QtWidgets.QPushButton("Select region")
                preview_button = QtWidgets.QPushButton("Preview")
                start_button = QtWidgets.QPushButton("Start drawing")

                layout.addWidget(self.status_label)
                layout.addWidget(self.image_label)
                layout.addWidget(self.api_key_input)
                layout.addWidget(browse_button)
                layout.addWidget(line_art_button)
                layout.addWidget(calibrate_button)
                layout.addWidget(preview_button)
                layout.addWidget(start_button)
                self.setCentralWidget(central)

                browse_button.clicked.connect(self._browse_image)
                line_art_button.clicked.connect(self._generate_line_art)
                calibrate_button.clicked.connect(self._select_region)
                preview_button.clicked.connect(self._preview)
                start_button.clicked.connect(self._start)

                hotkeys.register(
                    {
                        controller.session.hotkeys["calibrate"]: self._select_region,
                        controller.session.hotkeys["start"]: self._start,
                        controller.session.hotkeys["stop"]: controller.cancel,
                    }
                )

            def _browse_image(self) -> None:
                image_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self, "Select image", "", "Images (*.png *.jpg *.jpeg *.webp)"
                )
                if image_path:
                    controller.load_image(image_path)
                    self.image_label.setText(Path(image_path).name)
                    self.status_label.setText("Image loaded")

            def _generate_line_art(self) -> None:
                controller.gemini_client.settings.api_key = self.api_key_input.text().strip()
                try:
                    controller.generate_line_art()
                except Exception as exc:  # pragma: no cover
                    self.status_label.setText(str(exc))
                    return
                self.status_label.setText("Line art ready")

            def _select_region(self) -> None:
                try:
                    region = calibrator.select_region()
                except Exception as exc:  # pragma: no cover
                    self.status_label.setText(str(exc))
                    return
                controller.set_region(region)
                self.status_label.setText(f"Region: {region.bounds}")

            def _preview(self) -> None:
                try:
                    preview = controller.prepare_preview()
                except Exception as exc:  # pragma: no cover
                    self.status_label.setText(str(exc))
                    return
                self.status_label.setText(f"Preview ready: {preview.segment_count} segments")

            def _start(self) -> None:
                try:
                    controller.start_drawing()
                except Exception as exc:  # pragma: no cover
                    self.status_label.setText(str(exc))
                    return
                self.status_label.setText(controller.session.status)

            def closeEvent(self, event) -> None:
                self.hide()
                event.ignore()

        return MainWindow()


def build_default_window():
    from sts_draw.draw_executor import DrawExecutor
    from sts_draw.gemini_client import GeminiClient
    from sts_draw.preview_renderer import PreviewRenderer
    from sts_draw.stroke_planner import StrokePlanner

    controller = AppController(
        gemini_client=GeminiClient(),
        stroke_planner=StrokePlanner(),
        preview_renderer=PreviewRenderer(),
        draw_executor=DrawExecutor(),
    )
    calibrator = CanvasCalibrator()
    hotkeys = GlobalHotkeyManager()
    return MainWindowFactory().create(controller, calibrator, hotkeys)
