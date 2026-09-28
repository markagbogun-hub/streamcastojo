"""
streamer.py — drives ffmpeg to capture audio and push it to an Icecast2 or
Shoutcast v2 server over the modern HTTP source protocol. This is the
reliable, well-supported path (both server families accept ffmpeg's
standard 'icecast' muxer / HTTP PUT-style source connection).

Design notes:
- ffmpeg is run as a subprocess; stdout/stderr are drained on a background
  thread so the GUI never blocks and the process can't deadlock on a full
  pipe buffer.
- A callback fires on each ffmpeg log line (for a "connection log" panel)
  and on state changes (connecting / live / error / stopped).
- Now-playing metadata is pushed independently over HTTP so the encode
  doesn't need to restart every time the song changes:
    * Icecast2:   GET /admin/metadata?mount=<mount>&mode=updinfo&song=<title>
    * Shoutcast2: GET /admin.cgi?mode=updinfo&song=<title>  (via source login)
"""
from __future__ import annotations

import base64
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from config import AppConfig
from devices import find_ffmpeg
from mixer import build_mixer_ffmpeg_args


class StreamState(Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    LIVE = "live"
    RECONNECTING = "reconnecting"
    ERROR = "error"


LogCallback = Callable[[str], None]
StateCallback = Callable[[StreamState, str], None]  # state, human message
StatsCallback = Callable[[float, float], None]  # elapsed_seconds, bitrate_kbps

# ffmpeg progress lines look like:
#   size=     128kB time=00:00:08.19 bitrate= 128.0kbits/s speed=1.00x
# We only need the encoded-duration and measured-bitrate fields out of that.
PROGRESS_RE = re.compile(
    r"time=(?P<h>\d+):(?P<m>\d+):(?P<s>\d+\.?\d*).*?bitrate=\s*(?P<kbps>[\d.]+)kbits/s"
)


def parse_progress_line(line: str) -> Optional[tuple[float, float]]:
    """Pulls (elapsed_seconds, bitrate_kbps) out of an ffmpeg progress line,
    or None if this line isn't one. Shared by both streamers so the GUI's
    'derived' status readout behaves identically for either path."""
    m = PROGRESS_RE.search(line)
    if not m:
        return None
    elapsed = int(m.group("h")) * 3600 + int(m.group("m")) * 60 + float(m.group("s"))
    try:
        kbps = float(m.group("kbps"))
    except ValueError:
        return None
    return elapsed, kbps


CODEC_TO_FFMPEG = {
    "mp3": ["-c:a", "libmp3lame", "-f", "mp3"],
    "aac": ["-c:a", "aac", "-f", "adts"],
    "ogg": ["-c:a", "libvorbis", "-f", "ogg"],
}

CODEC_CONTENT_TYPE = {
    "mp3": "audio/mpeg",
    "aac": "audio/aac",
    "ogg": "application/ogg",
}


def _redact_command(cmd: list[str]) -> str:
    """Render an ffmpeg command for the UI log without exposing credentials."""
    redacted: list[str] = []
    for token in cmd:
        if "://" in token and "@" in token:
            prefix, rest = token.split("://", 1)
            if "@" in rest:
                credentials, destination = rest.split("@", 1)
                if ":" in credentials:
                    user, _password = credentials.split(":", 1)
                    token = f"{prefix}://{user}:***@{destination}"
        redacted.append(token)
    return " ".join(redacted)


class IcecastStreamer:
    """Handles Icecast2 / Shoutcast v2 streaming via ffmpeg's icecast muxer."""

    def __init__(
        self,
        config: AppConfig,
        on_log: LogCallback,
        on_state: StateCallback,
        on_stats: Optional[StatsCallback] = None,
    ):
        self.config = config
        self.on_log = on_log
        self.on_state = on_state
        self.on_stats = on_stats
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_requested = False
        self._auto_reconnect = True
        self._state = StreamState.STOPPED

    # -- public API ---------------------------------------------------

    def start(self) -> None:
        if self._proc is not None:
            return
        self._stop_requested = False
        self._auto_reconnect = True
        self._watchdog_thread = threading.Thread(target=self._run_with_reconnect, daemon=True)
        self._watchdog_thread.start()

    def stop(self) -> None:
        self._stop_requested = True
        self._auto_reconnect = False
        self._terminate_proc()
        self._set_state(StreamState.STOPPED, "Stopped")

    def push_metadata(self, song_title: str) -> bool:
        """Push a now-playing update without restarting the encode."""
        c = self.config.connection
        scheme = "https" if c.use_tls else "http"
        try:
            if c.protocol == "icecast2":
                url = (
                    f"{scheme}://{c.host}:{c.port}/admin/metadata?"
                    + urllib.parse.urlencode(
                        {"mount": c.mount, "mode": "updinfo", "song": song_title}
                    )
                )
                req = urllib.request.Request(url)
                auth = base64.b64encode(f"{c.username}:{c.password}".encode()).decode()
                req.add_header("Authorization", f"Basic {auth}")
            elif c.protocol == "shoutcast2":
                url = (
                    f"{scheme}://{c.host}:{c.port}/admin.cgi?"
                    + urllib.parse.urlencode({"mode": "updinfo", "song": song_title, "pass": c.password})
                )
                req = urllib.request.Request(url)
            else:
                self.on_log("[metadata] Shoutcast v1 metadata push not supported in this build.")
                return False

            with urllib.request.urlopen(req, timeout=8) as resp:
                ok = resp.status == 200
                self.on_log(f"[metadata] update sent, server responded {resp.status}")
                return ok
        except Exception as exc:
            self.on_log(f"[metadata] failed: {exc}")
            return False

    # -- internals ------------------------------------------------------

    def _set_state(self, state: StreamState, message: str) -> None:
        self._state = state
        self.on_state(state, message)

    def _build_command(self) -> list[str]:
        c = self.config.connection
        a = self.config.audio
        m = self.config.metadata

        ffmpeg = find_ffmpeg()
        # One dshow capture per enabled mixer source (mic, playback/loopback
        # device, or any other connected device), each passed through its own
        # fader and mixed down to a single stream before encoding.
        input_args, mix_args = build_mixer_ffmpeg_args(a.mixer_channels)
        if not input_args:
            raise ValueError("No active audio source. Enable at least one mixer device before going live.")

        codec_args = list(CODEC_TO_FFMPEG.get(a.codec, CODEC_TO_FFMPEG["mp3"]))
        codec_args += ["-b:a", f"{a.bitrate_kbps}k", "-ar", str(a.sample_rate), "-ac", str(a.channels)]

        scheme = "icecast"
        auth = f"{c.username}:{c.password}" if c.protocol == "icecast2" else f"source:{c.password}"
        # Shoutcast v2 source connections use the configured stream/mount
        # path as well. Falling back to "/" makes some DNAS builds accept the
        # TCP connection but reject the source because no stream is selected.
        mount = c.mount or "/stream"
        if not mount.startswith("/"):
            mount = "/" + mount
        target = f"{scheme}://{auth}@{c.host}:{c.port}{mount}"

        metadata_args = [
            "-ice_name", m.station_name,
            "-ice_description", m.description,
            "-ice_genre", m.genre,
            "-ice_url", m.website_url,
            "-ice_public", "1" if m.public else "0",
        ]

        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "info", "-y",
            *input_args,
            *mix_args,
            *codec_args,
            *metadata_args,
            "-content_type", CODEC_CONTENT_TYPE.get(a.codec, "audio/mpeg"),
            target,
        ]
        return cmd

    def _run_with_reconnect(self) -> None:
        backoff = 2
        while not self._stop_requested:
            self._set_state(StreamState.CONNECTING, "Connecting to server…")
            exit_code = self._run_once()
            if self._stop_requested:
                break
            if exit_code == 0:
                # Clean exit (user stopped, or ffmpeg finished normally).
                break
            if not self._auto_reconnect:
                self._set_state(StreamState.ERROR, "Stream ended unexpectedly.")
                break
            self._set_state(StreamState.RECONNECTING, f"Disconnected — retrying in {backoff}s…")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
        self._proc = None

    def _run_once(self) -> int:
        try:
            cmd = self._build_command()
        except ValueError as exc:
            self._set_state(StreamState.ERROR, str(exc))
            self.on_log(f"[stream] {exc}")
            return 2
        self.on_log("$ " + _redact_command(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except FileNotFoundError:
            self._set_state(StreamState.ERROR, "ffmpeg.exe not found. Reinstall the app or add ffmpeg to PATH.")
            return 1

        went_live = False
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.rstrip()
            if line:
                self.on_log(line)
            progress = parse_progress_line(line)
            if progress and self.on_stats:
                self.on_stats(*progress)
            if not went_live and ("size=" in line or "bitrate=" in line):
                went_live = True
                self._set_state(StreamState.LIVE, "Live")

        self._proc.wait()
        return self._proc.returncode or 0

    def _terminate_proc(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            except Exception:
                pass
        self._proc = None
