"""RadioCastOS music library and playlist persistence."""
from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass, asdict
import os

AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.aac', '.m4a'}

def data_dir() -> Path:
    appdata = os.environ.get('APPDATA')
    return Path(appdata) / 'RadioCastOS' if appdata else Path.home() / '.radiocastos'

LIBRARY_PATH = data_dir() / 'library.json'

@dataclass
class Track:
    path: str
    title: str
    artist: str = ''
    album: str = ''
    genre: str = ''
    year: int = 0
    bpm: float = 0.0
    energy: str = ''
    mood: str = ''
    language: str = ''
    gender: str = ''
    category: str = ''
    intro: float = 0.0
    outro: float = 0.0
    duration: float = 0.0

class MusicLibrary:
    def __init__(self):
        self.tracks: list[Track] = []
        self.playlist: list[str] = []
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(LIBRARY_PATH.read_text(encoding='utf-8')) if LIBRARY_PATH.exists() else {}
            self.tracks = [Track(**x) for x in raw.get('tracks', []) if isinstance(x, dict) and x.get('path')]
            self.playlist = [str(x) for x in raw.get('playlist', [])]
        except Exception:
            self.tracks, self.playlist = [], []

    def save(self) -> None:
        LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIBRARY_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps({'tracks': [asdict(t) for t in self.tracks], 'playlist': self.playlist}, indent=2), encoding='utf-8')
        os.replace(tmp, LIBRARY_PATH)

    def scan(self, folders: list[str]) -> int:
        existing = {os.path.normcase(os.path.abspath(t.path)): t for t in self.tracks}
        added = 0
        for folder in folders:
            root = Path(folder)
            if not root.exists():
                continue
            for p in root.rglob('*'):
                if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
                    continue
                key = os.path.normcase(str(p.resolve()))
                if key not in existing:
                    existing[key] = self._read_tags(p)
                    added += 1
        self.tracks = sorted(existing.values(), key=lambda t: (t.artist.lower(), t.title.lower()))
        self.save()
        return added

    def _read_tags(self, path: Path) -> Track:
        """Read common audio tags when Mutagen is available; always falls back safely."""
        t = Track(path=str(path.resolve()), title=path.stem)
        try:
            import mutagen
            audio = mutagen.File(str(path), easy=True)
            if audio:
                def first(key, default=''):
                    v = audio.get(key, [default])
                    return str(v[0]) if v else default
                t.title = first('title', t.title)
                t.artist = first('artist')
                t.album = first('album')
                t.genre = first('genre')
                try: t.year = int(first('date', '0')[:4])
                except Exception: t.year = 0
                try: t.bpm = float(first('bpm', '0'))
                except Exception: t.bpm = 0.0
                if getattr(audio, 'info', None):
                    t.duration = float(getattr(audio.info, 'length', 0.0) or 0.0)
        except Exception:
            pass
        return t

    def enrich_metadata(self) -> int:
        """Refresh tags for every known file without deleting operator metadata."""
        changed = 0
        for i, old in enumerate(self.tracks):
            fresh = self._read_tags(Path(old.path))
            # Preserve manually assigned programming fields.
            for field in ('energy','mood','language','gender','category','intro','outro'):
                setattr(fresh, field, getattr(old, field, getattr(fresh, field)))
            if (fresh.title, fresh.artist, fresh.album, fresh.genre, fresh.year, fresh.bpm, fresh.duration) != (old.title, old.artist, old.album, old.genre, old.year, old.bpm, old.duration):
                changed += 1
            self.tracks[i] = fresh
        self.save()
        return changed
    def add_to_playlist(self, paths: list[str]) -> None:
        known = {t.path for t in self.tracks}
        self.playlist.extend(p for p in paths if p in known and p not in self.playlist)
        self.save()

    def remove_from_playlist(self, paths: list[str]) -> None:
        remove = set(paths)
        self.playlist = [p for p in self.playlist if p not in remove]
        self.save()
