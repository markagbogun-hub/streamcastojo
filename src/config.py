"""
config.py — persistent settings for RadioCastOS.

Settings are stored as JSON in %APPDATA%\\RadioCastOS\\config.json on Windows
(falls back to the user's home directory on other platforms, which is only
relevant when running/testing this on macOS/Linux during development).
"""
from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List


def _config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "RadioCastOS"
    return Path.home() / ".radiocastos"


CONFIG_DIR = _config_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"


def _filtered(cls, data: dict) -> dict:
    """Keep only keys that are real fields of `cls`, so old config files
    (e.g. from before the mixer existed, with a lone 'input_device' key)
    load cleanly instead of raising and wiping out the whole section."""
    known = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in known}


@dataclass
class ConnectionSettings:
    protocol: str = "icecast2"       # "icecast2" | "shoutcast2" | "shoutcast1"
    host: str = "localhost"
    port: int = 8000
    mount: str = "/stream"           # unused for shoutcast v1
    username: str = "source"         # icecast2 source username
    password: str = ""
    use_tls: bool = False


@dataclass
class MixerChannel:
    """One audio source in the mixer: a mic, a playback/loopback device, or
    any other dshow-visible capture device — each with its own fader."""
    device: str                      # ffmpeg/dshow device name (the real key)
    label: str = ""                  # friendly display name, user-editable
    enabled: bool = True             # included in the mix when live
    muted: bool = False              # temporarily silenced without disabling
    volume: float = 1.0              # linear gain; fader shows this as 0-150%


@dataclass
class AudioSettings:
    mixer_channels: List[MixerChannel] = field(default_factory=list)
    sample_rate: int = 44100
    channels: int = 2                # output channel count: 1 mono / 2 stereo
    bitrate_kbps: int = 128
    codec: str = "mp3"               # "mp3" | "aac" | "ogg"


@dataclass
class RecordingSettings:
    directory: str = ""              # empty = default (~/Documents/RadioCastOS Recordings)
    format: str = "mp3"              # "mp3" | "wav"


@dataclass
class MetadataSettings:
    station_name: str = "My Station"
    genre: str = "Various"
    description: str = ""
    website_url: str = ""
    public: bool = False
    current_song: str = ""


@dataclass
class AppConfig:
    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    recording: RecordingSettings = field(default_factory=RecordingSettings)
    metadata: MetadataSettings = field(default_factory=MetadataSettings)

    @classmethod
    def load(cls) -> "AppConfig":
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

                audio_raw = dict(raw.get("audio", {}))
                mixer_raw = audio_raw.pop("mixer_channels", []) or []
                mixer_channels = [
                    MixerChannel(**_filtered(MixerChannel, mc))
                    for mc in mixer_raw
                    if isinstance(mc, dict) and mc.get("device")
                ]
                audio = AudioSettings(**_filtered(AudioSettings, audio_raw))
                audio.mixer_channels = mixer_channels

                return cls(
                    connection=ConnectionSettings(**_filtered(ConnectionSettings, raw.get("connection", {}))),
                    audio=audio,
                    recording=RecordingSettings(**_filtered(RecordingSettings, raw.get("recording", {}))),
                    metadata=MetadataSettings(**_filtered(MetadataSettings, raw.get("metadata", {}))),
                )
            except Exception:
                # Corrupt config: fall back to defaults rather than crashing.
                return cls()
        return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "connection": asdict(self.connection),
            "audio": asdict(self.audio),
            "recording": asdict(self.recording),
            "metadata": asdict(self.metadata),
        }
        # Write-then-replace so a power loss or interrupted write cannot leave
        # a half-written config.json that silently resets the app to defaults.
        tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp_path, CONFIG_PATH)
