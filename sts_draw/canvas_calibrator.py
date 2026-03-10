from __future__ import annotations

from dataclasses import dataclass, field

from sts_draw.models import CalibrationRegion, LineArtResult, PreviewPlacementResult


MIN_PREVIEW_SCALE = 0.1
MAX_PREVIEW_SCALE = 8.0
SCALE_STEP_FACTOR = 1.05


def _clamp_scale(value: float) -> float:
    return max(MIN_PREVIEW_SCALE, min(MAX_PREVIEW_SCALE, value))


@dataclass(slots=True)
class PreviewPlacementState:
    line_art_size: tuple[int, int]
    initial_scale: float = 1.0
    scale: float = field(init=False)
    center_x: int = field(init=False, default=0)
    center_y: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.scale = _clamp_scale(self.initial_scale)

    def move_to(self, x: int, y: int) -> None:
        self.center_x = x
        self.center_y = y

    def apply_wheel_steps(self, steps: float) -> None:
        self.scale = _clamp_scale(self.scale * (SCALE_STEP_FACTOR**steps))

    def current_size(self) -> tuple[int, int]:
        width = max(1, round(self.line_art_size[0] * self.scale))
        height = max(1, round(self.line_art_size[1] * self.scale))
        return width, height

    def current_region(self) -> CalibrationRegion:
        width, height = self.current_size()
        left = round(self.center_x - width / 2)
        top = round(self.center_y - height / 2)
        return CalibrationRegion(left=left, top=top, width=width, height=height)

    def confirm(self) -> PreviewPlacementResult:
        return PreviewPlacementResult(region=self.current_region(), scale=self.scale)


class CanvasCalibrator:
    def place_preview(self, line_art: LineArtResult, initial_scale: float = 1.0) -> PreviewPlacementResult:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError as exc:
            raise RuntimeError("PySide6 is not installed.") from exc

        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError("A QApplication instance is required.")

        pixmap = QtGui.QPixmap()
        if not pixmap.loadFromData(line_art.image_bytes):
            raise RuntimeError("Unable to decode preview image.")

        state = PreviewPlacementState(line_art.size, initial_scale=initial_scale)
        cursor_pos = QtGui.QCursor.pos()
        state.move_to(cursor_pos.x(), cursor_pos.y())

        loop = QtCore.QEventLoop()
        result: dict[str, PreviewPlacementResult | None] = {"placement": None}

        class Overlay(QtWidgets.QWidget):
            placed = QtCore.Signal(object)
            cancelled = QtCore.Signal()

            def __init__(self) -> None:
                super().__init__()
                self._pixmap = pixmap
                self._state = state
                screen = QtGui.QGuiApplication.primaryScreen()
                geometry = screen.virtualGeometry() if screen is not None else QtCore.QRect(0, 0, 1920, 1080)
                self.setGeometry(geometry)
                self.setWindowFlags(
                    QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool
                )
                self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
                self.setMouseTracking(True)
                self.setFocusPolicy(QtCore.Qt.StrongFocus)
                self.setCursor(QtCore.Qt.CrossCursor)

            def showEvent(self, event) -> None:
                super().showEvent(event)
                self.activateWindow()
                self.raise_()
                self.grabKeyboard()

            def closeEvent(self, event) -> None:
                self.releaseKeyboard()
                super().closeEvent(event)

            def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
                point = event.globalPosition().toPoint()
                self._state.move_to(point.x(), point.y())
                self.update()
                super().mouseMoveEvent(event)

            def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
                steps = event.angleDelta().y() / 120
                if steps:
                    self._state.apply_wheel_steps(steps)
                    self.update()
                event.accept()

            def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
                if event.button() == QtCore.Qt.LeftButton:
                    self.placed.emit(self._state.confirm())
                    self.close()
                    event.accept()
                    return
                if event.button() == QtCore.Qt.RightButton:
                    self.cancelled.emit()
                    self.close()
                    event.accept()
                    return
                super().mousePressEvent(event)

            def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
                if event.key() == QtCore.Qt.Key_Escape:
                    self.cancelled.emit()
                    self.close()
                    event.accept()
                    return
                super().keyPressEvent(event)

            def paintEvent(self, _event) -> None:
                painter = QtGui.QPainter(self)
                painter.setRenderHint(QtGui.QPainter.Antialiasing)
                painter.fillRect(self.rect(), QtGui.QColor(15, 17, 20, 42))

                region = self._state.current_region()
                geometry = self.geometry()
                rect = QtCore.QRect(
                    region.left - geometry.left(),
                    region.top - geometry.top(),
                    region.width,
                    region.height,
                )

                painter.setOpacity(0.78)
                painter.drawPixmap(rect, self._pixmap)
                painter.setOpacity(1.0)
                painter.setPen(QtGui.QPen(QtGui.QColor("#ff9f68"), 2))
                painter.drawRect(rect)

                info_rect = QtCore.QRect(24, 24, 360, 88)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(28, 24, 21, 190))
                painter.drawRoundedRect(info_rect, 14, 14)
                painter.setPen(QtGui.QColor("#fff8ef"))
                info_font = painter.font()
                info_font.setPointSize(11)
                painter.setFont(info_font)
                painter.drawText(
                    info_rect.adjusted(16, 12, -16, -12),
                    QtCore.Qt.AlignLeft | QtCore.Qt.TextWordWrap,
                    (
                        "滚轮缩放 · 左键确认 · 右键 / Esc 取消\n"
                        f"当前缩放 {self._state.scale * 100:.0f}% · 范围 {region.width} × {region.height}"
                    ),
                )

        overlay = Overlay()
        overlay.placed.connect(lambda placement: result.__setitem__("placement", placement))
        overlay.placed.connect(loop.quit)
        overlay.cancelled.connect(loop.quit)
        overlay.show()
        loop.exec()
        overlay.deleteLater()

        if result["placement"] is None:
            raise RuntimeError("Preview placement was cancelled.")
        return result["placement"]
