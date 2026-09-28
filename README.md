# RadioCastOS

**Professional Windows radio automation & streaming studio**

RadioCastOS is a complete desktop application for running an internet (or hybrid) radio station on Windows. It combines:

- Live streaming to Icecast2 / Shoutcast v2 (with best-effort Shoutcast v1 support)
- Multi-source audio mixer (mic, line-in, virtual cable / loopback)
- Local recording (independent of the live stream)
- Full music library + playlist player
- Clock-driven automation scheduler with imaging rules, commercial campaigns and advanced music rotation
- Commercial As-Run Log (printable reports + CSV export)
- Metadata management (artist, title, genre, BPM, energy, mood, etc.)

Everything is designed for real-world broadcast operators: persistent settings, auto-reconnect, live connection log, and a dark “broadcast console” UI.

---

## Features at a glance

| Area | Capabilities |
|------|--------------|
| **Streaming** | Icecast2 / Shoutcast v2 (TLS optional), legacy Shoutcast v1 mode, auto-reconnect with backoff, live bitrate / status |
| **Audio** | Per-source mixer with independent volume & mute, DirectShow device enumeration via ffmpeg, MP3 / AAC / OGG, configurable bitrate / sample rate / channels |
| **Recording** | Independent local recorder (MP3 or WAV) — works whether or not you are live |
| **Library** | Recursive folder scan, rich metadata (Mutagen when available), Metadata Manager, category buckets (Power, Current, Recurrent, Gold…) |
| **Automation** | Weekly clock schedules, program content buckets, imaging rules engine, commercial campaigns + make-goods, Advanced Music Scheduler (separation rules + daypart quotas), NEXT 10 preview, presenter takeover / Return to AutoDJ |
| **As-Run** | Every commercial that actually plays is logged; filterable report (print or CSV) |
| **Now Playing** | Station metadata + push updates without restarting the stream |

Settings and automation data live under `%APPDATA%\RadioCastOS\`.

---

## Why the build must run on Windows

Audio capture uses Windows DirectShow (`dshow`) devices through ffmpeg. The final artefact is a native Windows `.exe` + optional installer. Both require a real Windows environment.

You do **not** need a Windows PC yourself — the included GitHub Actions workflow builds everything on a Windows runner.

---

## Quick start – get the installer without a Windows machine

1. Create a new GitHub repository.
2. Push this entire folder to the repository (or upload as a zip and extract on GitHub).
3. Open the **Actions** tab.
4. The workflow **Build Windows installer** runs automatically on every push to `main` / `master` (and can be triggered manually).
5. When it finishes, download the artefacts:
   - **RadioCastOS-Windows-Installer** → `RadioCastOS-Setup.exe`
   - **RadioCastOS-Windows-Portable** → folder containing `RadioCastOS.exe` + ffmpeg

That’s it. The workflow downloads a static Windows ffmpeg build, runs PyInstaller, smoke-tests the binary, and produces the Inno Setup installer.

---

## Local build (if you have a Windows machine)

1. Place `ffmpeg.exe` and `ffplay.exe` (static Windows build) into the `assets\` folder.  
   Recommended source: https://www.gyan.dev/ffmpeg/builds/ (essentials build).
2. Double-click **`local-build.bat`**.

The script will:
- Install / upgrade PyInstaller
- Verify the Python source
- Build the portable application (`dist\RadioCastOS\RadioCastOS.exe`)
- If Inno Setup 6 is installed, also create `Output\RadioCastOS-Setup.exe`

Manual equivalent:

```powershell
pip install -r requirements.txt
# (ffmpeg.exe + ffplay.exe already in assets\)
pyinstaller build.spec
# → dist\RadioCastOS\RadioCastOS.exe

# With Inno Setup 6 installed:
ISCC installer.iss
# → Output\RadioCastOS-Setup.exe
```

---

## Running from source (development / quick test)

Still requires Windows for actual audio capture.

```powershell
pip install -r requirements.txt   # only needed for mutagen + pyinstaller
cd src
python main.py
```

`ffmpeg` / `ffplay` must be on `PATH` or discoverable by the app.

---

## Repository layout

```
src/                      Application source (Python 3.10+, stdlib + Tkinter)
  main.py                 Entry point
  app_gui.py              Main Tkinter UI (dark broadcast theme)
  streamer.py             Icecast2 / Shoutcast v2 streaming
  shoutcast_v1.py         Legacy Shoutcast v1 client
  devices.py              DirectShow device enumeration
  recorder.py             Local recording (independent ffmpeg process)
  mixer.py                Multi-source mixer model
  player.py               Local playlist / AutoDJ playback (ffplay)
  library.py              Music library + metadata
  scheduler.py            Automation engine, clocks, imaging rules, campaigns
  asrun.py                Commercial as-run log + printable reports
  config.py               Settings persistence

assets/
  icon.ico                Application icon (used by PyInstaller + installer)

build.spec                PyInstaller specification
installer.iss             Inno Setup script → RadioCastOS-Setup.exe
local-build.bat           One-click local Windows build
requirements.txt          Build-time dependencies (PyInstaller + optional mutagen)
.github/workflows/
  build-windows.yml       CI that produces the portable app + installer
```

---

## Requirements

- **Runtime (packaged app)**: Windows 10/11 64-bit. No Python installation needed.
- **Build**:
  - Python 3.10 – 3.12 recommended
  - PyInstaller 6.x
  - Inno Setup 6 (optional, for the installer)
  - Static Windows ffmpeg + ffplay binaries

The application itself uses only the Python standard library + Tkinter. Mutagen is optional and improves metadata import; the packaged build includes it when available.

---

## Version

**1.12.1** — Premium UI polish

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## Licence

MIT — see [LICENSE](LICENSE).

---

## Support / contributing

This is a self-contained project intended for operators who want a lightweight, open, Windows-native radio automation + streaming tool. Feel free to open issues or pull requests if you improve it.
