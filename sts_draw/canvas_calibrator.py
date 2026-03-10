from __future__ import annotations

from sts_draw.models import CalibrationRegion


class CanvasCalibrator:
    def select_region(self) -> CalibrationRegion:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets
        except ImportError as exc:
            raise RuntimeError("PySide6 is not installed.") from exc

        app = QtWidgets.QApplication.instance()
        if app is None:
            raise RuntimeError("A QApplication instance is required.")

        loop = QtCore.QEventLoop()
        result: dict[str, CalibrationRegion | None] = {"region": None}

        class Overlay(QtWidgets.QWidget):
            def __init__(self) -> None:
                super().__init__()
                self.origin = QtCore.QPoint()
                self.rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self)
                self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
                self.setWindowState(QtCore.Qt.WindowFullScreen)
                self.setWindowOpacity(0.3)

            def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
                self.origin = event.position().toPoint()
                self.rubber_band.setGeometry(QtCore.QRect(self.origin, QtCore.QSize()))
                self.rubber_band.show()

            def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
                rect = QtCore.QRect(self.origin, event.position().toPoint()).normalized()
                self.rubber_band.setGeometry(rect)

            def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
                rect = QtCore.QRect(self.origin, event.position().toPoint()).normalized()
                result["region"] = CalibrationRegion(left=rect.left(), top=rect.top(), width=rect.width(), height=rect.height())
                self.close()
                loop.quit()

        overlay = Overlay()
        overlay.show()
        loop.exec()
        if result["region"] is None:
            raise RuntimeError("Calibration was cancelled.")
        return result["region"]
