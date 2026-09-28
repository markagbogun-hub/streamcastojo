"""RadioCastOS professional radio-clock automation engine.

The engine is deliberately UI-independent. It combines weekly program
scheduling with wall-clock radio clocks, imaging rules, commercial breaks,
and a non-mutating NEXT-N preview.
"""
from __future__ import annotations
import datetime as dt
import functools, json, os, random, threading, re
import wave
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Callable, Optional

CATEGORIES = ("station_id", "jingle", "sweeper", "promo", "news", "commercial", "music")
MUSIC_CATEGORIES = ("power", "current", "recurrent", "gold", "oldies", "slow", "up_tempo")
DEFAULT_ROTATION = ("power", "current", "recurrent", "power", "gold", "oldies", "current", "up_tempo")


def data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "RadioCastOS" if appdata else Path.home() / ".radiocastos"

AUTOMATION_PATH = data_dir() / "automation.json"


@dataclass
class Program:
    name: str
    playlist: list[str] = field(default_factory=list)
    jingle: list[str] = field(default_factory=list)
    commercial: list[str] = field(default_factory=list)
    station_id: list[str] = field(default_factory=list)
    sweeper: list[str] = field(default_factory=list)
    promo: list[str] = field(default_factory=list)
    news: list[str] = field(default_factory=list)
    power: list[str] = field(default_factory=list)
    current: list[str] = field(default_factory=list)
    recurrent: list[str] = field(default_factory=list)
    gold: list[str] = field(default_factory=list)
    oldies: list[str] = field(default_factory=list)
    slow: list[str] = field(default_factory=list)
    up_tempo: list[str] = field(default_factory=list)


@dataclass
class ScheduleEntry:
    day: int
    start: str
    end: str
    program: str


@dataclass
class ClockEvent:
    minute: int
    category: str
    count: int = 1
    label: str = ""


@dataclass
class ClockTemplate:
    name: str
    start_hour: int
    end_hour: int
    events: list[ClockEvent] = field(default_factory=list)

    def active(self, hour: int) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour <= self.end_hour
        return hour >= self.start_hour or hour <= self.end_hour


@dataclass
class CommercialCampaign:
    """Commercial campaign with scheduling, pacing and make-good metadata."""
    name: str
    spots: list[str] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    daily_limit: int = 0
    hourly_limit: int = 0
    priority: int = 0
    spot_duration: int = 30
    enabled: bool = True
    played_count: int = 0
    missed_count: int = 0
    last_played: str = ""
    last_hour_key: str = ""
    hour_played_count: int = 0
    day_key: str = ""
    day_played_count: int = 0


@dataclass
class MusicRotation:
    enabled: bool = True
    sequence: list[str] = field(default_factory=lambda: list(DEFAULT_ROTATION))
    artist_separation: int = 2
    song_separation: int = 2

    def normalized(self) -> "MusicRotation":
        seq = [x for x in self.sequence if x in MUSIC_CATEGORIES] or list(DEFAULT_ROTATION)
        return MusicRotation(bool(self.enabled), seq, max(0, int(self.artist_separation)), max(0, int(self.song_separation)))


@dataclass
class MusicSchedulerRules:
    """Broadcast-grade music selection constraints and quotas."""
    enabled: bool = True
    artist_separation: int = 2
    title_separation: int = 2
    category_separation: int = 1
    recent_history: int = 12
    daypart_quotas: dict[str, dict[str, int]] = field(default_factory=lambda: {
        "Morning": {"power": 2, "current": 3, "recurrent": 2, "gold": 1},
        "Afternoon": {"power": 2, "current": 3, "recurrent": 2, "gold": 2},
        "Evening": {"power": 1, "current": 2, "recurrent": 2, "gold": 2},
        "Overnight": {"power": 0, "current": 1, "recurrent": 1, "gold": 2, "oldies": 2},
    })
    fallback_enabled: bool = True

    def normalized(self) -> "MusicSchedulerRules":
        clean = {}
        for dp, quota in (self.daypart_quotas or {}).items():
            clean[str(dp)] = {str(k): max(0, int(v)) for k, v in quota.items() if k in MUSIC_CATEGORIES}
        return MusicSchedulerRules(bool(self.enabled), max(0,int(self.artist_separation)), max(0,int(self.title_separation)),
                                   max(0,int(self.category_separation)), max(1,min(100,int(self.recent_history))), clean, bool(self.fallback_enabled))


@dataclass
class ClockRules:
    jingle_every: int = 0
    commercial_every: int = 3
    commercial_spots: int = 2
    station_id_every: int = 5
    preview_count: int = 10
    artist_separation: int = 2
    song_separation: int = 2

    def normalized(self) -> "ClockRules":
        return ClockRules(
            max(0, int(self.jingle_every)),
            max(0, int(self.commercial_every)),
            max(1, int(self.commercial_spots)),
            max(0, int(self.station_id_every)),
            max(1, min(50, int(self.preview_count))),
            max(0, int(self.artist_separation)),
            max(0, int(self.song_separation)),
        )



@dataclass
class ImagingRule:
    """Declarative station-imaging rule."""
    name: str
    trigger: str
    action: str
    interval: int = 1
    after: str = ""
    dayparts: list[str] = field(default_factory=list)
    enabled: bool = True
    priority: int = 50
    count: int = 1
    cooldown: int = 0

    def normalized(self) -> "ImagingRule":
        valid_triggers = {"song_count", "minute_interval", "top_of_hour", "after_category", "after_power"}
        valid_actions = set(CATEGORIES) | set(MUSIC_CATEGORIES)
        return ImagingRule(
            str(self.name),
            self.trigger if self.trigger in valid_triggers else "song_count",
            self.action if self.action in valid_actions else "sweeper",
            max(1, int(self.interval)), str(self.after or ""),
            [str(x) for x in self.dayparts if x], bool(self.enabled),
            int(self.priority), max(1, int(self.count)), max(0, int(self.cooldown))
        )


def default_imaging_rules() -> list[ImagingRule]:
    """Professional starter rules. Operators can edit/remove these; they are data."""
    rules = [
        ImagingRule("Top of Hour ID", "top_of_hour", "station_id", priority=100),
        ImagingRule("15 Minute Station ID", "minute_interval", "station_id", interval=15, priority=90),
        ImagingRule("After Commercial Jingle", "after_category", "jingle", after="commercial", priority=80),
        ImagingRule("Power Song Imaging", "after_power", "sweeper", priority=85),
    ]
    for daypart, songs in (("Morning", 3), ("Afternoon", 2), ("Evening", 3), ("Overnight", 4)):
        rules.append(ImagingRule(f"{daypart} Sweeper", "song_count", "sweeper",
                                 interval=songs, dayparts=[daypart], priority=70))
    return rules

def default_clock_templates() -> list[ClockTemplate]:
    # Minute positions are targets. If an item ends after a target, the next
    # eligible event is selected rather than attempting to interrupt audio.
    def events(include_news: bool = False) -> list[ClockEvent]:
        base = [
            ClockEvent(0, "station_id", label="Hour ID"),
            ClockEvent(2, "jingle", label="Jingle"),
            ClockEvent(3, "music"),
            ClockEvent(7, "music"),
            ClockEvent(11, "commercial", 2, "Commercial Break"),
            ClockEvent(14, "music"),
            ClockEvent(18, "sweeper", label="Sweeper"),
            ClockEvent(19, "music"),
            ClockEvent(24, "music"),
            ClockEvent(28, "station_id", label="Station ID"),
            ClockEvent(30, "music"),
            ClockEvent(34, "music"),
            ClockEvent(38, "commercial", 2, "Commercial Break"),
            ClockEvent(41, "music"),
            ClockEvent(46, "jingle", label="Jingle"),
            ClockEvent(47, "music"),
            ClockEvent(53, "promo", label="Promo"),
            ClockEvent(54, "music"),
        ]
        if include_news:
            base[0] = ClockEvent(0, "station_id", label="Hour ID + News Jingle")
            base.insert(1, ClockEvent(1, "jingle", label="News Jingle"))
            base.insert(2, ClockEvent(2, "news", 1, "Morning News"))
            for _event in base[3:]:
                if _event.minute >= 2:
                    _event.minute += 1
        return base
    return [
        ClockTemplate("Morning", 5, 11, events(True)),
        ClockTemplate("Afternoon", 12, 16, events(False)),
        ClockTemplate("Evening", 17, 21, events(False)),
        ClockTemplate("Overnight", 22, 4, events(False)),
    ]


@dataclass
class AutomationConfig:
    enabled: bool = False
    live_takeover: bool = True
    shuffle_music: bool = True
    default_program: str = "AutoDJ"
    programs: list[Program] = field(default_factory=lambda: [Program("AutoDJ")])
    schedule: list[ScheduleEntry] = field(default_factory=list)
    clock_rules: ClockRules = field(default_factory=ClockRules)
    clock_templates: list[ClockTemplate] = field(default_factory=default_clock_templates)
    campaigns: list[CommercialCampaign] = field(default_factory=list)
    commercial_break_target: int = 120
    avoid_consecutive_ads: bool = True
    music_rotation: MusicRotation = field(default_factory=MusicRotation)
    music_scheduler: MusicSchedulerRules = field(default_factory=MusicSchedulerRules)
    imaging_rules: list[ImagingRule] = field(default_factory=default_imaging_rules)


class AutomationStore:
    def __init__(self):
        self.config = AutomationConfig()
        self.load()

    def load(self):
        try:
            raw = json.loads(AUTOMATION_PATH.read_text(encoding="utf-8")) if AUTOMATION_PATH.exists() else {}
            self.config.enabled = bool(raw.get("enabled", False))
            self.config.live_takeover = bool(raw.get("live_takeover", True))
            self.config.shuffle_music = bool(raw.get("shuffle_music", True))
            self.config.default_program = str(raw.get("default_program", "AutoDJ"))
            self.config.programs = []
            for p in raw.get("programs", []):
                if isinstance(p, dict) and p.get("name"):
                    self.config.programs.append(Program(
                        name=str(p["name"]), playlist=list(p.get("playlist", [])),
                        jingle=list(p.get("jingle", [])), commercial=list(p.get("commercial", [])),
                        station_id=list(p.get("station_id", [])), sweeper=list(p.get("sweeper", [])),
                        promo=list(p.get("promo", [])), power=list(p.get("power", [])), current=list(p.get("current", [])),
                        recurrent=list(p.get("recurrent", [])), gold=list(p.get("gold", [])), oldies=list(p.get("oldies", [])),
                        slow=list(p.get("slow", [])), up_tempo=list(p.get("up_tempo", [])),
                    ))
            if not self.config.programs:
                self.config.programs = [Program("AutoDJ")]
            self.config.schedule = [
                ScheduleEntry(int(x["day"]), str(x["start"]), str(x["end"]), str(x["program"]))
                for x in raw.get("schedule", []) if isinstance(x, dict) and x.get("program")
            ]
            cr = raw.get("clock_rules", {})
            if isinstance(cr, dict):
                self.config.clock_rules = ClockRules(
                    int(cr.get("jingle_every", 0)), int(cr.get("commercial_every", 3)),
                    int(cr.get("commercial_spots", 2)), int(cr.get("station_id_every", 5)),
                    int(cr.get("preview_count", 10)), int(cr.get("artist_separation", 2)),
                    int(cr.get("song_separation", 2)),
                ).normalized()
            self.config.campaigns = []
            for x in raw.get("campaigns", []):
                if isinstance(x, dict) and x.get("name"):
                    self.config.campaigns.append(CommercialCampaign(
                        name=str(x["name"]), spots=list(x.get("spots", [])),
                        start_date=str(x.get("start_date", "")), end_date=str(x.get("end_date", "")),
                        daily_limit=max(0, int(x.get("daily_limit", 0))),
                        hourly_limit=max(0, int(x.get("hourly_limit", 0))),
                        priority=int(x.get("priority", 0)), spot_duration=max(1, int(x.get("spot_duration", 30))),
                        enabled=bool(x.get("enabled", True)), played_count=max(0, int(x.get("played_count", 0))),
                        missed_count=max(0, int(x.get("missed_count", 0))), last_played=str(x.get("last_played", "")),
                        last_hour_key=str(x.get("last_hour_key", "")), hour_played_count=max(0, int(x.get("hour_played_count", 0))),
                        day_key=str(x.get("day_key", "")), day_played_count=max(0, int(x.get("day_played_count", 0)))
                    ))
            self.config.commercial_break_target = max(0, int(raw.get("commercial_break_target", 120)))
            self.config.avoid_consecutive_ads = bool(raw.get("avoid_consecutive_ads", True))
            mr = raw.get("music_rotation", {})
            if isinstance(mr, dict):
                self.config.music_rotation = MusicRotation(bool(mr.get("enabled", True)), list(mr.get("sequence", DEFAULT_ROTATION)), int(mr.get("artist_separation", 2)), int(mr.get("song_separation", 2))).normalized()
            ms = raw.get("music_scheduler", {})
            if isinstance(ms, dict):
                self.config.music_scheduler = MusicSchedulerRules(
                    bool(ms.get("enabled", True)), int(ms.get("artist_separation", 2)), int(ms.get("title_separation", ms.get("song_separation", 2))),
                    int(ms.get("category_separation", 1)), int(ms.get("recent_history", 12)), dict(ms.get("daypart_quotas", {})), bool(ms.get("fallback_enabled", True))
                ).normalized()
            loaded_templates = []
            for t in raw.get("clock_templates", []):
                if not isinstance(t, dict) or not t.get("name"): continue
                evs = []
                for e in t.get("events", []):
                    try: evs.append(ClockEvent(int(e["minute"]), str(e["category"]), int(e.get("count", 1)), str(e.get("label", ""))))
                    except Exception: pass
                loaded_templates.append(ClockTemplate(str(t["name"]), int(t.get("start_hour", 0)), int(t.get("end_hour", 23)), evs))
            if loaded_templates:
                self.config.clock_templates = loaded_templates
            loaded_rules = []
            for r in raw.get("imaging_rules", []):
                if not isinstance(r, dict) or not r.get("name"): continue
                try:
                    loaded_rules.append(ImagingRule(
                        str(r["name"]), str(r.get("trigger", "song_count")),
                        str(r.get("action", "sweeper")), int(r.get("interval", 1)),
                        str(r.get("after", "")), list(r.get("dayparts", [])),
                        bool(r.get("enabled", True)), int(r.get("priority", 50)),
                        int(r.get("count", 1)), int(r.get("cooldown", 0))).normalized())
                except Exception: pass
            if loaded_rules:
                self.config.imaging_rules = loaded_rules
        except Exception:
            self.config = AutomationConfig()

    def save(self):
        AUTOMATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTOMATION_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.config), indent=2), encoding="utf-8")
        os.replace(tmp, AUTOMATION_PATH)


def _minutes(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def active_schedule(entries: list[ScheduleEntry], when: Optional[dt.datetime] = None) -> Optional[ScheduleEntry]:
    when = when or dt.datetime.now()
    minute, day = when.hour * 60 + when.minute, when.weekday()
    for e in entries:
        try: start, end = _minutes(e.start), _minutes(e.end)
        except Exception: continue
        if start <= end:
            if e.day == day and start <= minute < end: return e
        elif (e.day == day and minute >= start) or (e.day == (day - 1) % 7 and minute < end):
            return e
    return None


def track_profile(path: str) -> dict:
    """Best-effort broadcast metadata profile from Mutagen tags, with filename fallback."""
    profile = {"artist":"", "title":"", "album":"", "genre":"", "year":0, "bpm":0.0}
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if audio:
            def first(k, d=""):
                v=audio.get(k,[d]); return str(v[0]) if v else d
            profile["artist"]=first("artist"); profile["title"]=first("title"); profile["album"]=first("album"); profile["genre"]=first("genre")
            try: profile["year"]=int(first("date","0")[:4])
            except Exception: pass
            try: profile["bpm"]=float(first("bpm","0"))
            except Exception: pass
    except Exception:
        pass
    if not profile["title"] or not profile["artist"]:
        a,t=track_metadata_filename(path); profile["artist"] = profile["artist"] or a; profile["title"] = profile["title"] or t
    return profile

def track_metadata_filename(path: str) -> tuple[str, str]:
    """Best-effort artist/title extraction from common filename conventions.
    Falls back cleanly to the filename when tags are unavailable.
    """
    stem = Path(path).stem.strip()
    clean = re.sub(r"\s+", " ", stem)
    for sep in (" - ", " — ", " – ", "_-"):
        if sep in clean:
            artist, title = clean.split(sep, 1)
            if artist.strip() and title.strip():
                return artist.strip(), title.strip()
    return "Unknown Artist", clean or Path(path).name

@functools.lru_cache(maxsize=2048)
def _profile_cached(path: str, mtime: float) -> dict:
    return track_profile(path)


def track_metadata(path: str) -> tuple[str, str]:
    """(artist, title) from tags with filename fallback; cached per file version."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    profile = _profile_cached(path, mtime)
    return (profile.get("artist") or "Unknown Artist", profile.get("title") or Path(path).name)


class AutomationEngine:
    """Select broadcast content while keeping preview and playback state separate."""
    def __init__(self, store: AutomationStore, on_item: Callable[[str, str, str], None], on_state: Callable[[str], None] = lambda _x: None):
        self.store, self.on_item, self.on_state = store, on_item, on_state
        self.current_program: Optional[str] = None
        self._indexes = {c: 0 for c in CATEGORIES}
        self._last_music: Optional[str] = None
        self._music_index = 0
        self._rotation_index = 0
        self._recent_artists = []
        self._recent_songs = []
        self._started = False
        self._pending_category: Optional[str] = None
        self._pending_remaining = 0
        self._last_hour_key: Optional[tuple[int, int]] = None
        self._last_clock_minute = -1
        self._fired_clock_events: set[int] = set()
        self._last_ad_campaign: Optional[str] = None
        self._last_ad_path: Optional[str] = None
        self._rotation_index = 0
        self._recent_artists: list[str] = []
        self._recent_songs: list[str] = []
        self._previewing = False
        self._play_history: list[dict] = []
        self._last_selection_reason = ""
        self._last_category: Optional[str] = None
        self._last_item_time: Optional[dt.datetime] = None
        self._rule_last_fired: dict[str, dt.datetime] = {}
        self._songs_since_rule: dict[str, int] = {}
        self._lock = threading.Lock()

    def enabled_now(self) -> bool:
        c = self.store.config
        return bool(c.enabled and (not c.schedule or active_schedule(c.schedule) is not None))

    def program_now(self) -> Optional[Program]:
        c = self.store.config
        entry = active_schedule(c.schedule)
        name = entry.program if entry else c.default_program
        return next((p for p in c.programs if p.name == name), c.programs[0] if c.programs else None)

    def _content(self, p: Program):
        return {c: [x for x in getattr(p, "playlist", []) if x] if c == "music" else [x for x in getattr(p, c, []) if x] for c in CATEGORIES}

    def _campaign_active(self, c: CommercialCampaign, when: dt.datetime) -> bool:
        if not c.enabled or not c.spots:
            return False
        today = when.date().isoformat()
        if c.start_date and today < c.start_date:
            return False
        if c.end_date and today > c.end_date:
            return False
        hour_key = when.strftime("%Y-%m-%d-%H")
        if c.day_key != today:
            c.day_key, c.day_played_count = today, 0
        if c.last_hour_key != hour_key:
            c.last_hour_key, c.hour_played_count = hour_key, 0
        if c.daily_limit and c.day_played_count >= c.daily_limit:
            return False
        if c.hourly_limit and c.hour_played_count >= c.hourly_limit:
            return False
        return True

    def _pick_commercial(self, p: Program, when: dt.datetime) -> Optional[str]:
        campaigns = [c for c in self.store.config.campaigns if self._campaign_active(c, when)]
        campaigns.sort(key=lambda c: (-c.priority, c.played_count, c.name.lower()))
        if campaigns:
            # Prefer a campaign that is not the previous campaign; this prevents
            # two consecutive spots from the same campaign when alternatives exist.
            if self.store.config.avoid_consecutive_ads and len(campaigns) > 1 and self._last_ad_campaign:
                alternatives = [c for c in campaigns if c.name != self._last_ad_campaign]
                if alternatives:
                    campaigns = alternatives + [c for c in campaigns if c.name == self._last_ad_campaign]
            campaign = campaigns[0]
            candidates = [x for x in campaign.spots if x]
            if self.store.config.avoid_consecutive_ads and len(candidates) > 1 and self._last_ad_path in candidates:
                candidates = [x for x in candidates if x != self._last_ad_path] or candidates
            path = candidates[campaign.played_count % len(candidates)]
            campaign.played_count += 1
            campaign.hour_played_count += 1
            campaign.day_played_count += 1
            campaign.last_played = when.isoformat(timespec="seconds")
            self._last_ad_campaign, self._last_ad_path = campaign.name, path
            if not self._previewing:
                self.store.save()
            return path
        # Legacy commercial pool remains a fallback.
        return self._pick("commercial", self._content(p)["commercial"])

    def record_missed_commercial(self, campaign_name: str, count: int = 1) -> None:
        for c in self.store.config.campaigns:
            if c.name == campaign_name:
                c.missed_count += max(1, int(count))
                self.store.save()
                return

    def campaign_report(self) -> list[dict]:
        return [asdict(c) for c in self.store.config.campaigns]

    def _artist_key(self, path: str) -> str:
        return track_metadata(path)[0].strip().lower()

    def _song_key(self, path: str) -> str:
        return track_metadata(path)[1].strip().lower()

    def _remember_play(self, category: str, path: str, reason: str) -> None:
        artist, title = track_metadata(path)
        self._last_selection_reason = reason
        self._play_history.append({"time": dt.datetime.now().isoformat(timespec="seconds"),
                                   "category": category, "artist": artist, "title": title, "path": path, "reason": reason})
        self._play_history = self._play_history[-100:]

    def rotation_history(self, limit: int = 50) -> list[dict]:
        return list(self._play_history[-max(1, int(limit)):])

    def why_next(self, item: tuple[str, str, str] | None) -> str:
        if not item:
            return "No eligible item."
        category, path, _ = item
        if category not in MUSIC_CATEGORIES:
            return f"Clock/imaging event: {category.replace('_', ' ').title()}"
        rotation = self.store.config.music_rotation.normalized()
        return (f"Rotation category: {category.replace('_',' ').title()} • "
                f"artist separation: {rotation.artist_separation} • "
                f"song separation: {rotation.song_separation}")

    def _pick_music(self, p: Program) -> Optional[tuple[str, str]]:
        rotation = self.store.config.music_rotation.normalized()
        rules = self.store.config.music_scheduler.normalized()
        pools = {cat: [x for x in getattr(p, cat, []) if x] for cat in MUSIC_CATEGORIES}
        if not any(pools.values()):
            path = self._pick("music", self._content(p)["music"])
            if path: self._remember_play("music", path, "Fallback: no categorized music pools")
            return ("music", path) if path else None
        sequence = rotation.sequence if rotation.enabled else ["current"]
        daypart = self.active_clock().name
        quota = rules.daypart_quotas.get(daypart, {})
        recent_categories = [h.get("category") for h in self._play_history[-rules.category_separation:] if h.get("category")]
        attempts = []
        # Prefer the configured rotation while respecting daypart minimum quotas.
        ordered = []
        for cat in sequence:
            if cat not in ordered: ordered.append(cat)
        for cat, minimum in quota.items():
            if minimum > 0 and cat in pools and cat not in ordered: ordered.append(cat)
        for _ in range(len(ordered) * 2):
            if not ordered: break
            cat = ordered[self._rotation_index % len(ordered)]
            self._rotation_index += 1
            candidates = pools.get(cat, [])[:]
            if not candidates: continue
            # Quota pressure: if a category has a minimum and has not appeared enough this daypart, prioritize it.
            if quota.get(cat, 0) > 0:
                recent_cat_count = sum(1 for h in self._play_history[-max(1, len(ordered)*2):] if h.get("category") == cat)
                if recent_cat_count >= quota[cat] and len(ordered) > 1:
                    pass
            strict = [x for x in candidates
                      if self._song_key(x) not in self._recent_songs
                      and self._artist_key(x) not in self._recent_artists
                      and (not rules.category_separation or cat not in recent_categories)]
            relaxed = [x for x in candidates if self._song_key(x) not in self._recent_songs and self._artist_key(x) not in self._recent_artists]
            title_only = [x for x in candidates if self._song_key(x) not in self._recent_songs]
            choices = strict or relaxed or title_only or (candidates if rules.fallback_enabled else [])
            if not choices: continue
            path = random.choice(choices) if self.store.config.shuffle_music else choices[0]
            artist, song = self._artist_key(path), self._song_key(path)
            self._recent_artists.append(artist); self._recent_songs.append(song)
            self._recent_artists = self._recent_artists[-max(1, rules.artist_separation):]
            self._recent_songs = self._recent_songs[-max(1, rules.title_separation):]
            self._music_index += 1
            profile = track_profile(path)
            meta = []
            if profile.get("genre"): meta.append(f"genre {profile["genre"]}")
            if profile.get("year"): meta.append(f"year {profile["year"]}")
            if profile.get("bpm"): meta.append(f"BPM {profile["bpm"]:.0f}")
            reason = f"Category {cat.title()} • {daypart} clock • artist sep {rules.artist_separation} • title sep {rules.title_separation}" + (" • " + " • ".join(meta) if meta else "")
            if strict: reason += " • all separation rules satisfied"
            elif relaxed: reason += " • relaxed category separation"
            elif title_only: reason += " • relaxed artist separation"
            else: reason += " • fallback: pool exhausted"
            self._remember_play(cat, path, reason)
            return cat, path
        return None

    def _pick(self, category: str, items: list[str]) -> Optional[str]:
        if not items: return None
        if category == "music":
            choices = items[:]
            if self.store.config.shuffle_music and len(choices) > 1 and self._last_music in choices: choices.remove(self._last_music)
            path = random.choice(choices) if self.store.config.shuffle_music else choices[self._indexes[category] % len(choices)]
            self._last_music, self._indexes[category] = path, self._indexes[category] + 1
            self._music_index += 1
            return path
        idx = self._indexes[category]
        path = items[idx % len(items)]
        self._indexes[category] = idx + 1
        return path

    def active_clock(self, when: Optional[dt.datetime] = None) -> ClockTemplate:
        when = when or dt.datetime.now()
        templates = self.store.config.clock_templates or default_clock_templates()
        return next((t for t in templates if t.active(when.hour)), templates[-1])

    def clock_name(self, when: Optional[dt.datetime] = None) -> str:
        return self.active_clock(when).name

    def next_clock_event(self, when: Optional[dt.datetime] = None) -> Optional[ClockEvent]:
        when = when or dt.datetime.now()
        events = self.active_clock(when).events
        minute = when.minute
        due = [e for e in events if e.minute > minute]
        return min(due, key=lambda e: e.minute) if due else (events[0] if events else None)

    def seconds_to_next_event(self, when: Optional[dt.datetime] = None) -> int:
        when = when or dt.datetime.now()
        event = self.next_clock_event(when)
        if not event: return 0
        delta = (event.minute - when.minute) * 60 - when.second
        if delta <= 0: delta += 3600
        return delta

    def _rule_daypart_ok(self, rule: ImagingRule, when: dt.datetime) -> bool:
        return not rule.dayparts or self.active_clock(when).name in rule.dayparts

    def _rule_due(self, rule: ImagingRule, when: dt.datetime) -> bool:
        if not rule.enabled or not self._rule_daypart_ok(rule, when):
            return False
        last = self._rule_last_fired.get(rule.name)
        if last and rule.cooldown and (when - last).total_seconds() < rule.cooldown * 60:
            return False
        if rule.trigger == "top_of_hour":
            return self._last_hour_key != (when.date().toordinal(), when.hour)
        if rule.trigger == "minute_interval":
            return last is None or (when - last).total_seconds() >= rule.interval * 60
        if rule.trigger == "song_count":
            return self._songs_since_rule.get(rule.name, 0) >= rule.interval
        if rule.trigger == "after_power":
            return self._last_category == "power"
        if rule.trigger == "after_category":
            return bool(rule.after) and self._last_category == rule.after
        return False

    def _fire_imaging_rule(self, rule: ImagingRule, p: Program, when: dt.datetime) -> Optional[tuple[str, str]]:
        category = rule.action
        if category == "music":
            picked = self._pick_music(p)
        else:
            path = self._pick_commercial(p, when) if category == "commercial" else self._pick(category, self._content(p).get(category, []))
            picked = (category, path) if path else None
        if not picked:
            return None
        self._rule_last_fired[rule.name] = when
        self._songs_since_rule[rule.name] = 0
        self._last_selection_reason = f"Imaging rule: {rule.name}"
        return picked

    def imaging_rules(self) -> list[ImagingRule]:
        return [r.normalized() for r in self.store.config.imaging_rules]

    def _reset_program(self, p: Program):
        self.current_program = p.name
        self._indexes = {c: 0 for c in CATEGORIES}
        self._last_music = None
        self._music_index = 0
        self._rotation_index = 0
        self._recent_artists = []
        self._recent_songs = []
        self._started = False
        self._pending_category = None
        self._pending_remaining = 0
        self._last_hour_key = None
        self._last_clock_minute = -1
        self._fired_clock_events = set()
        self._last_category = None
        self._last_item_time = None
        self._rule_last_fired = {}
        self._songs_since_rule = {}
        now = dt.datetime.now()
        for rule in self.imaging_rules():
            if rule.trigger == "minute_interval":
                self._rule_last_fired[rule.name] = now
        self.on_state(f"Program: {p.name} • {self.clock_name()}")

    def next_item(self, when: Optional[dt.datetime] = None) -> Optional[tuple[str, str, str]]:
        when = when or dt.datetime.now()
        with self._lock:
            p = self.program_now()
            if not p: return None
            if self.current_program != p.name: self._reset_program(p)
            content, rules = self._content(p), self.store.config.clock_rules.normalized()
            hour_key = (when.date().toordinal(), when.hour)

            # 1.7.0: rules are evaluated by priority. No fixed Jingle -> Ad ->
            # Music sequence is assumed.
            due_rules = sorted((r for r in self.imaging_rules() if self._rule_due(r, when)),
                               key=lambda r: (-r.priority, r.name.lower()))
            for rule in due_rules:
                picked = self._fire_imaging_rule(rule, p, when)
                if picked:
                    cat, path = picked
                    if rule.trigger == "top_of_hour":
                        self._last_hour_key = hour_key
                    self._last_category, self._last_item_time = cat, when
                    return cat, path, p.name

            if not self._started:
                self._started = True
                for cat in ("jingle", "station_id"):
                    path = self._pick(cat, content[cat])
                    if path:
                        self._last_category, self._last_item_time = cat, when
                        return cat, path, p.name

            # Clock target: choose the latest event whose minute has been reached.
            clock = self.active_clock(when)
            due = [e for e in clock.events if e.minute <= when.minute and e.minute not in self._fired_clock_events]
            if due:
                event = max(due, key=lambda e: e.minute)
                self._last_clock_minute = event.minute
                self._fired_clock_events.add(event.minute)
                if event.category == "music":
                    pass
                else:
                    path = self._pick_commercial(p, when) if event.category == "commercial" else self._pick(event.category, content.get(event.category, []))
                    if path:
                        if event.count > 1:
                            self._pending_category, self._pending_remaining = event.category, event.count - 1
                        return event.category, path, p.name

            # Backward compatibility for pre-1.7 configurations only.
            if not self.store.config.imaging_rules:
                if rules.station_id_every and self._music_index and self._music_index % rules.station_id_every == 0:
                    path = self._pick("station_id", content["station_id"])
                    if path: return "station_id", path, p.name
                if rules.jingle_every and self._music_index and self._music_index % rules.jingle_every == 0:
                    path = self._pick("jingle", content["jingle"])
                    if path: return "jingle", path, p.name
                if rules.commercial_every and self._music_index and self._music_index % rules.commercial_every == 0:
                    path = self._pick_commercial(p, when)
                    if path:
                        self._pending_category, self._pending_remaining = "commercial", max(0, rules.commercial_spots - 1)
                        return "commercial", path, p.name

            picked = self._pick_music(p)
            if picked:
                cat, path = picked
                self._last_category, self._last_item_time = cat, when
                for rule in self.imaging_rules():
                    if rule.trigger == "song_count" and rule.enabled:
                        self._songs_since_rule[rule.name] = self._songs_since_rule.get(rule.name, 0) + 1
                return cat, path, p.name
            for cat in ("jingle", "commercial", "station_id", "sweeper", "promo"):
                path = self._pick(cat, content[cat])
                if path:
                    self._last_category, self._last_item_time = cat, when
                    return cat, path, p.name
            return None

    def preview(self, count: Optional[int] = None, when: Optional[dt.datetime] = None) -> list[tuple[str, str, str]]:
        count = max(1, min(50, int(count or self.store.config.clock_rules.preview_count)))
        when = when or dt.datetime.now()
        state = (self.current_program, self._indexes.copy(), self._last_music, self._music_index,
                 self._started, self._pending_category, self._pending_remaining,
                 self._last_hour_key, self._last_clock_minute, self._fired_clock_events.copy(), self._last_ad_campaign, self._last_ad_path,
                 list(self._play_history), self._last_selection_reason,
                 self._last_category, self._last_item_time, dict(self._rule_last_fired), dict(self._songs_since_rule))
        out = []
        cursor = when
        self._previewing = True
        try:
            for _ in range(count):
                item = self.next_item(cursor)
                if item:
                    out.append(item)
                    # Preview is deliberately non-real-time: advance a small
                    # virtual clock so upcoming clock events become visible.
                    cursor += dt.timedelta(minutes=2)
                else:
                    break
        finally:
            self._previewing = False
            (self.current_program, self._indexes, self._last_music, self._music_index,
             self._started, self._pending_category, self._pending_remaining,
             self._last_hour_key, self._last_clock_minute, self._fired_clock_events, self._last_ad_campaign, self._last_ad_path,
             self._play_history, self._last_selection_reason,
             self._last_category, self._last_item_time, self._rule_last_fired, self._songs_since_rule) = state
        return out
