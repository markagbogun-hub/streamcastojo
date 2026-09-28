"""CI helper: verify all application modules import cleanly."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

MODULES = [
    "main",
    "app_gui",
    "scheduler",
    "player",
    "streamer",
    "recorder",
    "devices",
    "mixer",
    "library",
    "asrun",
    "shoutcast_v1",
    "config",
]

def main() -> int:
    failed = []
    for name in MODULES:
        try:
            __import__(name)
            print(f"  OK  {name}")
        except Exception as exc:
            print(f"FAIL  {name}: {exc}")
            failed.append(name)
    if failed:
        print(f"Import validation failed for: {', '.join(failed)}")
        return 1
    print("Source imports OK")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
