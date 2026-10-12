import ctypes
import sys

from PySide6.QtWidgets import QApplication

from .icon import make_icon
from .ui import MainWindow


def main() -> int:
    if sys.platform == "win32":
        # Without its own ID Windows groups the window under python.exe and shows Python's icon.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SasLogistics.Planner")
    app = QApplication(sys.argv)
    app.setWindowIcon(make_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
