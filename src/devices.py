"""
devices.py — enumerate Windows audio capture devices via ffmpeg's dshow
device lister, so the GUI can present a friendly dropdown (mic, line-in,
or a virtual cable like VB-Audio Virtual Cable / VoiceMeeter for routing
desktop/app audio into the stream).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List

# Keyword heuristics used only to pick a sensible *default* label and
# mixer-enabled state for a newly discovered device. Purely a convenience —
# the user can always relabel, re-enable, mute, or re-fader any source.
MIC_KEYWORDS = ("microphone", "mic", "headset", "webcam", "usb audio")
PLAYBACK_KEYWORDS = (
    "stereo mix", "wave out mix", "what u hear", "loopback",
    "virtual cable", "voicemeeter", "vb-audio", "cable output",
)


def categorize_device(name: str) -> str:
    """Best-effort guess at a device's role: 'mic', 'playback', or 'other'."""
    lowered = name.lower()
    if any(k in lowered for k in PLAYBACK_KEYWORDS):
        return "playback"
    if any(k in lowered for k in MIC_KEYWORDS):
        return "mic"
    return "other"


def default_label_for(name: str) -> str:
    kind = categorize_device(name)
    if kind == "mic":
        return "Microphone"
    if kind == "playback":
        return "Playback Audio"
    return name


def _find_tool(name: str) -> str:
    """Locate a bundled or system FFmpeg tool (ffmpeg / ffplay).

    Search order: PyInstaller bundle folder (_internal), the folder next to the
    exe, this source folder and its ../assets, then the system PATH. Falls back
    to the bare name so subprocess reports a clear 'not found' error.
    """
    exe_name = name + (".exe" if sys.platform.startswith("win") else "")
    folders = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        folders.append(Path(meipass))
    folders.append(Path(sys.executable).resolve().parent)
    here = Path(__file__).resolve().parent
    folders.extend([here, here.parent / "assets"])
    for folder in folders:
        candidate = folder / exe_name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name) or name


def find_ffmpeg() -> str:
    """Return the ffmpeg executable path (bundled copy preferred)."""
    return _find_tool("ffmpeg")


def find_ffplay() -> str:
    """Return the ffplay executable path (bundled copy preferred)."""
    return _find_tool("ffplay")


def list_audio_devices(ffmpeg_path: str | None = None) -> List[str]:
    """
    Runs: ffmpeg -list_devices true -f dshow -i dummy
    and parses the DirectShow audio device names out of stderr.

    ffmpeg intentionally exits non-zero for this call — that's expected,
    not an error condition; we only care about stderr text.
    """
    exe = ffmpeg_path or find_ffmpeg()
    try:
        proc = subprocess.run(
            [exe, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    text = proc.stderr or ""
    devices: List[str] = []
    in_audio_section = False
    for line in text.splitlines():
        if "DirectShow audio devices" in line:
            in_audio_section = True
            continue
        if "DirectShow video devices" in line:
            in_audio_section = False
            continue
        if in_audio_section:
            m = re.search(r'"([^"]+)"', line)
            if m:
                devices.append(m.group(1))
    return devices
