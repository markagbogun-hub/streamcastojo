"""
shoutcast_v1.py — best-effort legacy source client for old Shoutcast v1
DNAS servers, which use a non-HTTP-compliant handshake that ffmpeg's
'icecast' muxer does not speak. This module hand-rolls the connection:

  1. Open a raw TCP socket to host:port.
  2. Send the source password as the very first line, bare, followed by
     CRLF (this is the part that breaks strict HTTP libraries — a plain
     password line before any request line is legal to Shoutcast v1 but
     not to an HTTP-conformant client).
  3. Server replies "OK2" (or "OK" on very old builds) to accept, or
     "invalid password" to reject.
  4. Send ICY-style header lines (icy-name, icy-genre, icy-pub, etc.)
     terminated by a blank line.
  5. Stream raw MP3 frames (Shoutcast v1 is MP3-only) as the body —
     no chunked encoding, just a continuous byte stream.

Audio is supplied by piping raw MP3 bytes from an ffmpeg subprocess into
this socket, so we still get device capture + encoding from ffmpeg, we
just replace ffmpeg's own network transport with this manual handshake.

This mode is best-effort: DNAS v1 servers vary in strictness across
versions, and this is not a substitute for testing against a real server.
"""
from __future__ import annotations

import socket
import subprocess
import threading
from typing import Callable, Optional

from config import AppConfig
from devices import find_ffmpeg
from mixer import build_mixer_ffmpeg_args
from streamer import StatsCallback, parse_progress_line

LogCallback = Callable[[str], None]

CHUNK_SIZE = 4096
SOCKET_TIMEOUT_S = 10


class ShoutcastV1Streamer:
    def __init__(self, config: AppConfig, on_log: LogCallback, on_stats: Optional[StatsCallback] = None):
        self.config = config
        self.on_log = on_log
        self.on_stats = on_stats
        self._sock: Optional[socket.socket] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stop_requested = False

    def start(self) -> bool:
        c = self.config.connection
        a = self.config.audio
        m = self.config.metadata

        self.on_log(f"[shoutcast1] Connecting to {c.host}:{c.port} ...")
        try:
            sock = socket.create_connection((c.host, c.port), timeout=SOCKET_TIMEOUT_S)
        except OSError as exc:
            self.on_log(f"[shoutcast1] Connection failed: {exc}")
            return False

        try:
            # Step 1: bare password line, CRLF-terminated.
            sock.sendall((c.password + "\r\n").encode("ascii", errors="replace"))

            # Step 2: read the server's one-line accept/reject response.
            sock.settimeout(SOCKET_TIMEOUT_S)
            resp = b""
            while b"\r\n" not in resp and len(resp) < 256:
                chunk = sock.recv(64)
                if not chunk:
                    break
                resp += chunk
            resp_text = resp.decode("ascii", errors="replace").strip()
            self.on_log(f"[shoutcast1] Server said: {resp_text!r}")

            if "invalid password" in resp_text.lower():
                self.on_log("[shoutcast1] Rejected: invalid password.")
                sock.close()
                return False
            if not (resp_text.startswith("OK")):
                self.on_log("[shoutcast1] Unexpected handshake response; aborting.")
                sock.close()
                return False

            # Step 3: ICY header block.
            headers = (
                f"icy-name:{m.station_name}\r\n"
                f"icy-genre:{m.genre}\r\n"
                f"icy-url:{m.website_url}\r\n"
                f"icy-pub:{1 if m.public else 0}\r\n"
                f"icy-br:{a.bitrate_kbps}\r\n"
                "\r\n"
            )
            sock.sendall(headers.encode("ascii", errors="replace"))
        except OSError as exc:
            self.on_log(f"[shoutcast1] Handshake failed: {exc}")
            sock.close()
            return False

        self._sock = sock
        self._stop_requested = False

        # Spin up ffmpeg to capture+encode MP3 to stdout, then pump those
        # bytes into the raw socket ourselves.
        ffmpeg = find_ffmpeg()
        input_args, mix_args = build_mixer_ffmpeg_args(a.mixer_channels)
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "info",
            *input_args,
            *mix_args,
            "-c:a", "libmp3lame", "-b:a", f"{a.bitrate_kbps}k",
            "-ar", str(a.sample_rate), "-ac", str(a.channels),
            "-f", "mp3", "-",
        ]
        try:
            self._ffmpeg_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=False,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except FileNotFoundError:
            self.on_log("[shoutcast1] ffmpeg.exe not found.")
            sock.close()
            return False

        self._pump_thread = threading.Thread(target=self._pump_audio, daemon=True)
        self._pump_thread.start()
        # ffmpeg's own progress reporting comes out on stderr (stdout is the
        # raw MP3 bytes we're pumping to the socket) — read it on its own
        # thread so the GUI can show measured bitrate/elapsed time instead
        # of just raw log spam, same as the Icecast/Shoutcast v2 path.
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        self.on_log("[shoutcast1] Live (legacy v1 mode).")
        return True

    def _drain_stderr(self) -> None:
        assert self._ffmpeg_proc is not None and self._ffmpeg_proc.stderr is not None
        for raw_line in self._ffmpeg_proc.stderr:
            line = raw_line.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            progress = parse_progress_line(line)
            if progress and self.on_stats:
                self.on_stats(*progress)
            else:
                self.on_log("[shoutcast1] " + line)

    def _pump_audio(self) -> None:
        assert self._ffmpeg_proc is not None and self._ffmpeg_proc.stdout is not None
        try:
            while not self._stop_requested:
                data = self._ffmpeg_proc.stdout.read(CHUNK_SIZE)
                if not data:
                    break
                if self._sock is not None:
                    self._sock.sendall(data)
        except OSError as exc:
            self.on_log(f"[shoutcast1] Stream error: {exc}")
        finally:
            self.on_log("[shoutcast1] Audio pump stopped.")

    def stop(self) -> None:
        self._stop_requested = True
        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
            self._ffmpeg_proc.terminate()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self.on_log("[shoutcast1] Stopped.")
