"""Local playlist playback engine for RadioCastOS.

Uses ffplay from the bundled/system FFmpeg installation so the automation
engine stays lightweight and dependency-free on Windows.
"""
from __future__ import annotations
import subprocess, threading, time
from pathlib import Path
from typing import Callable, Optional

LogCallback = Callable[[str], None]
TrackCallback = Callable[[str, bool], None]  # path, ended

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

class PlaylistPlayer:
    def __init__(self, find_player: Callable[[], str], on_log: LogCallback, on_track: TrackCallback):
        self.find_player = find_player
        self.on_log = on_log
        self.on_track = on_track
        self.playlist: list[str] = []
        self.index = -1
        self._proc: Optional[subprocess.Popen] = None
        self._stop_requested = False
        self._lock = threading.Lock()
        self._auto_advance = True
        self._automation_callback = None
        self._takeover_proc: Optional[subprocess.Popen] = None
        self._takeover_path: Optional[str] = None
        self._automation_paused_for_takeover = False


    def takeover_active(self) -> bool:
        return self._takeover_proc is not None and self._takeover_proc.poll() is None

    def begin_takeover(self) -> bool:
        """Pause the automation process without changing its track/index.

        A separate ffplay process is then available for presenter/live-assist
        audio. This preserves the AutoDJ process and its exact playback
        position so RETURN TO AUTODJ can resume rather than restart the song.
        """
        if not self.is_playing():
            self._automation_paused_for_takeover = False
            return True
        if self._automation_paused_for_takeover:
            return True
        if self.pause():
            self._automation_paused_for_takeover = True
            self.on_log("[player] AutoDJ paused for presenter takeover.")
            return True
        return False

    def play_takeover_file(self, path: str) -> bool:
        """Play a presenter-selected file while AutoDJ remains paused."""
        if not path or not Path(path).exists():
            return False
        if not self._automation_paused_for_takeover and self.is_playing():
            if not self.begin_takeover():
                return False
        self._stop_takeover_process()
        try:
            ffplay = self.find_player()
            cmd = [ffplay, "-hide_banner", "-loglevel", "warning",
                   "-nodisp", "-autoexit", "-vn", path]
            self._takeover_path = path
            self._takeover_proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW
            )
            threading.Thread(target=self._watch_takeover, args=(self._takeover_proc, path), daemon=True).start()
            self.on_log(f"[player] Presenter audio: {Path(path).name}")
            self.on_track(path, False)
            return True
        except (FileNotFoundError, OSError) as exc:
            self.on_log(f"[player] Could not start presenter audio: {exc}")
            return False

    def end_takeover(self, resume: bool = True) -> bool:
        self._stop_takeover_process()
        was_paused = self._automation_paused_for_takeover
        self._automation_paused_for_takeover = False
        if resume and was_paused and self.is_playing() is False and self._proc is not None:
            return self.resume_from_takeover()
        if resume and was_paused and self._proc is not None:
            return self.resume_from_takeover()
        return True

    def resume_from_takeover(self) -> bool:
        if not self._proc or self._proc.poll() is not None:
            self._automation_paused_for_takeover = False
            return False
        if not self._automation_paused_for_takeover:
            return True
        if self.pause():
            self._automation_paused_for_takeover = False
            self.on_log("[player] AutoDJ resumed from presenter takeover.")
            return True
        return False

    def _watch_takeover(self, proc: subprocess.Popen, path: str) -> None:
        proc.wait()
        if self._takeover_proc is proc:
            self._takeover_proc = None
            self._takeover_path = None
        if proc.returncode == 0 and not self._stop_requested:
            self.on_track(path, True)

    def _stop_takeover_process(self) -> None:
        proc = self._takeover_proc
        if proc and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write("q")
                    proc.stdin.flush()
                proc.wait(timeout=2)
            except Exception:
                try: proc.terminate()
                except Exception: pass
        self._takeover_proc = None
        self._takeover_path = None

    def set_auto_advance(self, enabled: bool) -> None:
        self._auto_advance = bool(enabled)

    def set_automation_callback(self, callback) -> None:
        self._automation_callback = callback

    def play_file(self, path: str) -> bool:
        """Play one automation asset without changing the manual playlist."""
        if not path:
            return False
        self._stop_process()
        self._stop_requested = False
        with self._lock:
            self.playlist = [path]
            self.index = 0
        return self._launch_current()

    def set_playlist(self, paths: list[str]) -> None:
        with self._lock:
            self.playlist = list(paths)
            if not self.playlist:
                self.index = -1
            elif self.index >= len(self.playlist):
                self.index = len(self.playlist) - 1

    def current(self) -> Optional[str]:
        with self._lock:
            return self.playlist[self.index] if 0 <= self.index < len(self.playlist) else None

    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def play(self, index: Optional[int] = None) -> bool:
        with self._lock:
            if index is not None:
                if not (0 <= index < len(self.playlist)):
                    return False
                self.index = index
            elif self.index < 0:
                if not self.playlist:
                    return False
                self.index = 0
        self._stop_process()
        self._stop_requested = False
        return self._launch_current()

    def _launch_current(self) -> bool:
        path = self.current()
        if not path or not Path(path).exists():
            self.on_log("[player] Track missing; skipping.")
            return self.next()
        try:
            ffplay = self.find_player()
            cmd = [ffplay, "-hide_banner", "-loglevel", "warning",
                   "-nodisp", "-autoexit", "-vn", path]
            self.on_log(f"[player] Playing: {Path(path).name}")
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW
            )
            threading.Thread(target=self._watch, daemon=True).start()
            self.on_track(path, False)
            return True
        except (FileNotFoundError, OSError) as exc:
            self.on_log(f"[player] Could not start ffplay: {exc}")
            return False

    def _watch(self) -> None:
        proc = self._proc
        if not proc:
            return
        proc.wait()
        if self._proc is proc:
            self._proc = None
        if not self._stop_requested and proc.returncode == 0:
            path = self.current() or ""
            self.on_track(path, True)
            if self._automation_callback:
                try:
                    self._automation_callback(path)
                except Exception as exc:
                    self.on_log(f"[automation] callback failed: {exc}")
            elif self._auto_advance:
                self.next()

    def pause(self) -> bool:
        # ffplay uses the 'p' key to pause/resume.
        if not self._proc or self._proc.poll() is not None or not self._proc.stdin:
            return False
        try:
            self._proc.stdin.write("p")
            self._proc.stdin.flush()
            return True
        except Exception:
            return False

    def stop(self) -> None:
        self._stop_requested = True
        self._stop_takeover_process()
        self._automation_paused_for_takeover = False
        self._stop_process()

    def next(self) -> bool:
        with self._lock:
            if not self.playlist:
                return False
            self.index = (self.index + 1) % len(self.playlist)
        self._stop_process()
        self._stop_requested = False
        return self._launch_current()

    def previous(self) -> bool:
        with self._lock:
            if not self.playlist:
                return False
            self.index = (self.index - 1) % len(self.playlist)
        self._stop_process()
        self._stop_requested = False
        return self._launch_current()

    def _stop_process(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.write("q")
                    proc.stdin.flush()
                proc.wait(timeout=2)
            except Exception:
                try: proc.terminate()
                except Exception: pass
        self._proc = None
