from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6 import QtWidgets
    except ImportError:
        print("PySide6 is not installed. Install dependencies from requirements.txt first.")
        return 1

    from sts_draw.ui import build_default_window

    app = QtWidgets.QApplication(sys.argv)
    window = build_default_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
