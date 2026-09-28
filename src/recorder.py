"""
recorder.py — "live recording on PC" support.

This is a standalone local recorder: its own ffmpeg process, capturing
whatever sources are currently active in the Audio mixer (mic, playback/
loopback device, etc.) straight to a file on disk. It's completely
independent of the Icecast/Shoutcast streamer, so recording works the
same way whether you're live, about to go live, or just testing your
levels — start/stop it whenever you like from the Recording tab.

Because it opens the same dshow devices as the streamer, running both at
once relies on Windows/the device driver allowing more than one reader —
true for most virtual/loopback devices (VB-Audio Virtual Cable,
VoiceMeeter, Stereo Mix) but not guaranteed for every physical microphone
driver. If a device refuses the second open, the recording's ffmpeg
process will fail fast and log the error rather than silently produce an
empty file.
"""
from __future__ import annotations

import datetime
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from config import AppConfig
from devices import find_ffmpeg
from mixer import build_mixer_ffmpeg_args

LogCallback = Callable[[str], None]

RECORD_CODEC_ARGS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "wav": ["-c:a", "pcm_s16le"],
}

RECORD_EXT = {"mp3": "mp3", "wav": "wav"}

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def default_recordings_dir() -> Path:
    """~/Documents/RadioCastOS Recordings, falling back to the home
    directory if Documents doesn't exist (e.g. when testing off Windows)."""
    documents = Path.home() / "Documents"
    base = documents if documents.exists() else Path.home()
    return base / "RadioCastOS Recordings"


def build_output_path(directory: str, fmt: str, station_name: str = "") -> Path:
    """One timestamped file per recording session, named after the
    station so a folder of recordings stays identifiable at a glance."""
    folder = Path(directory) if directory else default_recordings_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_name = "".join(c for c in station_name if c.isalnum() or c in " _-").strip()
    safe_name = safe_name or "RadioCastOS"
    ext = RECORD_EXT.get(fmt, "mp3")
    return folder / f"{safe_name}_{stamp}.{ext}"


class Recorder:
    """One recording session. Create a fresh instance per start() call
    (the GUI does this for you) rather than reusing one across sessions."""

    def __init__(self, config: AppConfig, on_log: LogCallback):
        self.config = config
        self.on_log = on_log
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self.output_path: Optional[Path] = None
        self.started_at: Optional[datetime.datetime] = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> Optional[Path]:
        if self.is_running():
            return self.output_path

        a = self.config.audio
        r = self.config.recording
        ffmpeg = find_ffmpeg()
        input_args, mix_args = build_mixer_ffmpeg_args(a.mixer_channels)
        if not input_args:
            self.on_log("[record] No active audio source. Enable at least one mixer device first.")
            return None
        codec_args = list(RECORD_CODEC_ARGS.get(r.format, RECORD_CODEC_ARGS["mp3"]))
        codec_args += ["-ar", str(a.sample_rate), "-ac", str(a.channels)]

        self.output_path = build_output_path(r.directory, r.format, self.config.metadata.station_name)

        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "info", "-y",
            *input_args,
            *mix_args,
            *codec_args,
            str(self.output_path),
        ]
        self.on_log("[record] $ " + " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            self.on_log("[record] ffmpeg.exe not found. Reinstall the app or add ffmpeg to PATH.")
            self.output_path = None
            return None

        self.started_at = datetime.datetime.now()
        self._reader_thread = threading.Thread(target=self._drain, daemon=True)
        self._reader_thread.start()
        self.on_log(f"[record] Recording started -> {self.output_path}")
        return self.output_path

    def _drain(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        for line in self._proc.stdout:
            line = line.rstrip()
            if line:
                self.on_log("[record] " + line)

    def stop(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                # Ask ffmpeg to shut down the way an interactive 'q' keypress
                # would, so it finalizes the output file's header/trailer
                # properly instead of leaving a truncated file behind.
                if proc.stdin:
                    try:
                        proc.stdin.write("q")
                        proc.stdin.flush()
                    except Exception:
                        pass
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
            self.on_log(f"[record] Recording stopped. Saved to {self.output_path}")
        self._proc = None
