# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for RadioCastOS Windows builds."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPEC).resolve().parent
SRC = ROOT / "src"
ASSETS = ROOT / "assets"

ENTRY = SRC / "main.py"
ICON = ASSETS / "icon.ico"
FFMPEG = ASSETS / "ffmpeg.exe"
FFPLAY = ASSETS / "ffplay.exe"

if not ENTRY.exists():
    raise SystemExit(f"Missing application entry point: {ENTRY}")

if not FFMPEG.exists():
    raise SystemExit(
        f"Missing {FFMPEG}. Download/stage the Windows FFmpeg runtime before building."
    )

if not FFPLAY.exists():
    raise SystemExit(
        f"Missing {FFPLAY}. Download/stage the Windows FFmpeg runtime before building."
    )

# All application modules are imported from src. Listing them explicitly keeps
# the build deterministic. collect_submodules('mutagen') prevents optional
# metadata readers from disappearing from the frozen application.
APP_MODULES = [
    "app_gui",
    "asrun",
    "config",
    "devices",
    "library",
    "mixer",
    "player",
    "recorder",
    "scheduler",
    "shoutcast_v1",
    "streamer",
]

HIDDEN_IMPORTS = APP_MODULES + collect_submodules("mutagen")

binaries = [
    (str(FFMPEG), "."),
    (str(FFPLAY), "."),
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RadioCastOS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RadioCastOS",
)
