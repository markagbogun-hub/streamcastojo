"""
main.py — RadioCastOS entry point.
Run directly with `python main.py`, or build with PyInstaller (see
build.spec) for a standalone Windows .exe.
"""
from app_gui import RadioCastApp


def main() -> None:
    app = RadioCastApp()
    app.mainloop()


if __name__ == "__main__":
    main()
