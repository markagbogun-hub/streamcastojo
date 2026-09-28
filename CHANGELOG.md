# RadioCastOS Changelog

## 1.12.1 — Premium UI polish
- Refined dark broadcast palette with higher contrast and calmer surfaces.
- New button styles: Accent (CTA), Danger, Success, Ghost.
- Premium top bar with brand mark and subtle product line.
- Improved status bar and connection log presentation.
- Dashboard hero and metric cards tightened for a more professional look.
- Consistent typography scale and spacing throughout the main shell.
- Larger default window size for modern displays.

## 1.12.0 — Commercial As-Run Log
- Every commercial spot that actually starts playing is recorded with its date, time, campaign, spot name and program.
- New Automation button **Commercial As-Run Log**: filter by date range and campaign, then Print Report or Export CSV.
- Printable report (opens in the browser) includes a summary per campaign, a numbered play log and signature lines.
- Log file: `%APPDATA%\RadioCastOS\commercial_log.csv` (opens in Excel).
- Fixed: `track_metadata` was missing from `scheduler.py`, which broke the Next 10 preview and rotation separation. Tag lookups are now cached.

## 1.11.2 — Startup fix
- Fixed a startup import error.
- Made ffmpeg / ffplay discovery work reliably inside the packaged application.

## 1.11.1 — Packaging cleanup
- Removed stray backup and cache files.
- `local-build.bat` now requires both `ffmpeg.exe` and `ffplay.exe`.
- Changelog put into chronological order.

## 1.11.0 — Intelligent Music Library & Metadata
- Library supports broadcast-programming metadata: genre, year, BPM, energy, mood, language, gender, category, intro and outro.
- **Library → Metadata Manager** to inspect / edit records.
- Mutagen (when available) imports common audio tags automatically; otherwise filename fallback is used.

## 1.10.0 — Advanced Music Scheduler
- Music selector is now a rules engine rather than a simple random / sequence picker.
- Configure artist / title / category separation, daypart quotas, history size and graceful fallback from **Automation → Advanced Music Scheduler**.
- Each selection records a human-readable reason for audit and troubleshooting.

## 1.9.0 — News & Scheduled Segments + Broadcast Clock Editor
- Added first-class `news` content category to station programs.
- News available in automation content assignment and as a rules-engine action.
- Morning clock sequence support (Hour ID → News Jingle → News → music).
- Visual Broadcast Clock Editor in the Automation tab.
- Preserved 1.7.x JSON compatibility.

## 1.8.0 — Commercial Campaigns & Make-Goods
- Commercial Campaigns with date ranges, priority and spot lists.
- Campaign selection before the legacy commercial pool.
- Commercial Campaigns operator dialog.
- Make-good tracking API for missed commercial spots.
- Kept commercial fallback compatibility with existing 1.4 / 1.5 configurations.

## 1.7.0 — Station Imaging & Rules Engine
- Replaced rigid imaging sequences with a declarative station-rules layer.
- Rules can trigger on: every N songs (daypart-aware), elapsed wall-clock minutes, top of hour, after a Commercial break, after a Power song.
- Actions: Station ID, Jingle, Sweeper, Promo, Commercial or Music.
- Rules evaluated by priority and skipped when their content bucket is empty.
- **Station Imaging Rules** editor in the Automation tab (add / edit / delete / enable / disable / restore defaults).
- Existing automation JSON remains compatible; new `imaging_rules` section is written while older fields are retained.
- Default starter rules produce a professional clock (top-of-hour ID, daypart sweepers, post-commercial jingles, etc.).

## 1.5.0 — Professional Radio Clock
- Configurable station clock rules for commercial breaks, spot counts and station IDs.
- Non-mutating NEXT 10 automation preview.
- Live operator clock and next-item display.
- One-click Return to AutoDJ after presenter takeover.
- Improved commercial break sequencing so multi-spot breaks complete before music resumes.
- Morning / Afternoon / Evening / Overnight clock templates.
- Wall-clock top-of-hour station ID handling.
- Imaging categories for sweepers and promos while retaining 1.4.x JSON compatibility.
- Automation UI controls for Play Next and Emergency Stop.
- Removed unreachable legacy scheduler code.

## 1.4.0 — Radio Automation Scheduler
- Clock-driven weekly program scheduling.
- Program content buckets: Jingle, Commercial, Music and Station ID.
- 24/7 AutoDJ sequencing with automatic next-item playback.
- Presenter takeover so manual playlist control can interrupt automation.
- Persistent automation configuration under `%APPDATA%\RadioCastOS\automation.json`.
- Cross-midnight schedule handling and schedule-aware program switching.

## 1.3.0 — Local Automation Player
- ffplay-based local playlist playback engine.
- Automatic next-track playback and Previous / Next controls.
- Play / Pause / Resume / Stop controls in Library & Playlist.
- Now-playing metadata follows locally playing tracks.
- Player status shown in the operator UI.

## 1.2.0 — Library & Playlist Foundation
- Persistent Music Library.
- Recursive music-folder scanning for MP3, WAV, FLAC, OGG, AAC and M4A.
- Playlist management with add / remove / clear actions.
- Library & Playlist operator tab.
- Library data stored separately from station streaming configuration.
- Prepared the application for the next automation stage (scheduler and playback engine).

## 1.0 – 1.1 — Core streaming foundation
- Icecast2 / Shoutcast v2 streaming with ffmpeg.
- Best-effort Shoutcast v1 support.
- Multi-source mixer, device enumeration, local recording.
- Dark broadcast-console UI, connection log, persisted settings.
