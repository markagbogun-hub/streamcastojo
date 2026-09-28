"""
app_gui.py — Premium dark-theme front-end for RadioCastOS.

Broadcast-console UI with Dashboard, Connection, Audio, Recording,
Now Playing, Library & Playlist, and Automation tabs, plus a persistent
status bar and connection log.

Built on stdlib Tkinter/ttk only so the PyInstaller build stays small
and dependency-free beyond ffmpeg itself.
"""
from __future__ import annotations

import datetime
import queue
import socket
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from library import MusicLibrary
from player import PlaylistPlayer
import asrun
from scheduler import AutomationStore, AutomationEngine, active_schedule, CommercialCampaign, MUSIC_CATEGORIES, DEFAULT_ROTATION

from config import AppConfig, MixerChannel
from devices import categorize_device, default_label_for, list_audio_devices, find_ffplay
from recorder import Recorder, default_recordings_dir
from shoutcast_v1 import ShoutcastV1Streamer
from streamer import IcecastStreamer, StreamState

APP_TITLE = "RadioCastOS"

# ---------------------------------------------------------------------------
# Premium dark broadcast palette
# Inspired by modern broadcast desks + refined dark product UIs.
# High contrast for readability under studio lighting, restrained accents.
# ---------------------------------------------------------------------------
BG           = "#0b0c0e"      # deepest background
BG_ALT       = "#08090a"      # log / recessed areas
PANEL        = "#141618"      # cards, fields
PANEL_RAISED = "#1c1e22"      # elevated surfaces, header
LINE         = "#2a2d33"      # subtle borders
LINE_SOFT    = "#22252a"      # even softer dividers
TEXT         = "#f0f1f3"      # primary text
TEXT_DIM     = "#9b9ea5"      # secondary
TEXT_FAINT   = "#5c5f66"      # tertiary / disabled
AMBER        = "#f0a53a"      # primary accent / caution / CTA
AMBER_DIM    = "#c4842e"      # pressed / secondary amber
RED          = "#ef4444"      # on-air error / danger
GREEN        = "#34d399"      # live / healthy
BLUE         = "#60a5fa"      # info

STATE_COLORS = {
    StreamState.STOPPED: TEXT_FAINT,
    StreamState.CONNECTING: AMBER,
    StreamState.LIVE: GREEN,
    StreamState.RECONNECTING: AMBER,
    StreamState.ERROR: RED,
}

# Preferred UI font stack (Windows first)
FONT_UI      = ("Segoe UI", 9)
FONT_UI_SEMI = ("Segoe UI Semibold", 9)
FONT_UI_MED  = ("Segoe UI Semibold", 10)
FONT_UI_LG   = ("Segoe UI Semibold", 12)
FONT_UI_XL   = ("Segoe UI Semibold", 16)
FONT_UI_HERO = ("Segoe UI Semibold", 20)
FONT_MONO    = ("Consolas", 9)
FONT_MONO_LG = ("Consolas", 16, "bold")


class RadioCastApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1020x820")
        self.minsize(880, 700)

        self.config_obj = AppConfig.load()
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._is_legacy_v1_running = False
        self._stream_started_at = None
        self._last_bitrate = 0.0
        self._last_elapsed = 0.0

        self.streamer = IcecastStreamer(
            self.config_obj,
            on_log=self._queue_log,
            on_state=self._on_state_change,
            on_stats=self._on_stats,
        )
        self.v1_streamer: ShoutcastV1Streamer | None = None
        self.recorder: Recorder | None = None
        self.library = MusicLibrary()
        self.player = PlaylistPlayer(find_ffplay, on_log=self._queue_log, on_track=self._on_player_track)
        self.automation_store = AutomationStore()
        self.automation = AutomationEngine(
            self.automation_store,
            on_item=self._automation_item_started,
            on_state=self._automation_state,
        )
        self._automation_manual_takeover = False
        self._automation_owned = False
        self._automation_takeover_active = False

        self._apply_theme()
        self._build_widgets()
        self._load_into_widgets()
        self.after(150, self._drain_log_queue)
        self.after(500, self._tick_recording_timer)
        self.after(1000, self._tick_dashboard)
        self.after(1000, self._tick_automation)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Theming
    # ------------------------------------------------------------------

    def _apply_theme(self) -> None:
        """Premium dark broadcast-console theme.

        Uses ttk 'clam' because native Windows themes ignore most color
        customisation on Entry / Combobox / Notebook.
        """
        self.configure(bg=BG)
        try:
            self.tk.call("tk", "scaling", 1.15)  # slightly denser modern feel
        except tk.TclError:
            pass

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Base
        style.configure(
            ".",
            background=BG,
            foreground=TEXT_DIM,
            fieldbackground=PANEL,
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            troughcolor=PANEL,
            focuscolor=AMBER,
            font=FONT_UI,
        )

        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("Header.TLabel", background=BG, foreground=TEXT, font=FONT_UI_LG)
        style.configure("TLabel", background=BG, foreground=TEXT_DIM, font=FONT_UI)
        style.configure("Dim.TLabel", background=BG, foreground=TEXT_FAINT, font=FONT_UI)
        style.configure("Value.TLabel", background=PANEL, foreground=TEXT, font=FONT_UI_MED)
        style.configure("CardTitle.TLabel", background=PANEL, foreground=TEXT_FAINT, font=("Segoe UI Semibold", 8))

        # LabelFrames (section cards)
        style.configure(
            "TLabelframe",
            background=BG,
            bordercolor=LINE,
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=BG,
            foreground=TEXT_DIM,
            font=FONT_UI_SEMI,
        )

        # Notebook tabs — clean, modern, no heavy borders
        style.configure(
            "TNotebook",
            background=BG,
            bordercolor=BG,
            tabmargins=(8, 6, 8, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=PANEL_RAISED,
            foreground=TEXT_DIM,
            padding=(18, 9),
            font=FONT_UI_SEMI,
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", BG), ("active", PANEL)],
            foreground=[("selected", TEXT), ("active", TEXT)],
        )

        # Standard buttons
        style.configure(
            "TButton",
            background=PANEL_RAISED,
            foreground=TEXT,
            bordercolor=LINE,
            padding=(12, 8),
            font=FONT_UI,
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("active", LINE), ("pressed", PANEL), ("disabled", PANEL)],
            foreground=[("disabled", TEXT_FAINT)],
        )

        # Primary CTA (Go Live, primary actions)
        style.configure(
            "Accent.TButton",
            background=AMBER,
            foreground="#0b0c0e",
            bordercolor=AMBER,
            padding=(14, 9),
            font=FONT_UI_MED,
            relief="flat",
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#fbbf5d"), ("pressed", AMBER_DIM)],
            foreground=[("disabled", "#5c4a20")],
        )

        # Danger (Stop / Emergency)
        style.configure(
            "Danger.TButton",
            background="#3b1c1c",
            foreground=RED,
            bordercolor="#5c2a2a",
            padding=(12, 8),
            font=FONT_UI_SEMI,
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#4c2424"), ("pressed", "#2a1212")],
        )

        # Success / Live secondary
        style.configure(
            "Success.TButton",
            background="#14352a",
            foreground=GREEN,
            bordercolor="#1e4d3a",
            padding=(12, 8),
            font=FONT_UI_SEMI,
        )
        style.map(
            "Success.TButton",
            background=[("active", "#1a4435")],
        )

        # Ghost / quiet secondary actions
        style.configure(
            "Ghost.TButton",
            background=BG,
            foreground=TEXT_DIM,
            bordercolor=LINE_SOFT,
            padding=(10, 7),
            font=FONT_UI,
        )
        style.map(
            "Ghost.TButton",
            background=[("active", PANEL)],
            foreground=[("active", TEXT)],
        )

        # Entries & Comboboxes
        style.configure(
            "TEntry",
            fieldbackground=PANEL,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=LINE,
            padding=6,
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", AMBER)],
            lightcolor=[("focus", AMBER)],
            darkcolor=[("focus", AMBER)],
        )

        style.configure(
            "TCombobox",
            fieldbackground=PANEL,
            background=PANEL,
            foreground=TEXT,
            arrowcolor=TEXT_DIM,
            bordercolor=LINE,
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", PANEL), ("focus", PANEL)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", PANEL)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", AMBER)],
        )

        # Combobox dropdown (native listbox under the hood on Windows)
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", AMBER)
        self.option_add("*TCombobox*Listbox.selectForeground", "#0b0c0e")
        self.option_add("*TCombobox*Listbox.font", FONT_UI)

        style.configure("TCheckbutton", background=BG, foreground=TEXT_DIM, font=FONT_UI)
        style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", TEXT)])

        style.configure("TRadiobutton", background=BG, foreground=TEXT_DIM, font=FONT_UI)
        style.map("TRadiobutton", background=[("active", BG)])

        style.configure(
            "Horizontal.TScale",
            background=BG,
            troughcolor=PANEL,
            bordercolor=LINE,
            lightcolor=AMBER,
            darkcolor=AMBER,
        )
        style.configure("TSeparator", background=LINE)
        style.configure(
            "Vertical.TScrollbar",
            background=PANEL_RAISED,
            troughcolor=BG_ALT,
            bordercolor=LINE,
            arrowcolor=TEXT_DIM,
            relief="flat",
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", LINE)],
        )

        # Progress / subtle meters
        style.configure(
            "TProgressbar",
            background=AMBER,
            troughcolor=PANEL,
            bordercolor=LINE,
            lightcolor=AMBER,
            darkcolor=AMBER,
        )

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def _build_widgets(self) -> None:
        # ── Premium top bar ──────────────────────────────────────────────
        header = tk.Frame(self, bg=PANEL_RAISED, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)

        # Left brand mark
        mark = tk.Frame(header, bg=PANEL_RAISED)
        mark.pack(side="left", padx=(18, 0), pady=0)
        tk.Label(
            mark, text="RADIOCAST", bg=PANEL_RAISED, fg=TEXT,
            font=("Segoe UI Semibold", 13),
        ).pack(side="left")
        tk.Label(
            mark, text=" OS", bg=PANEL_RAISED, fg=AMBER,
            font=("Segoe UI Semibold", 13),
        ).pack(side="left")

        # Subtle version / product line on the right of header
        tk.Label(
            header, text="Broadcast Console", bg=PANEL_RAISED, fg=TEXT_FAINT,
            font=("Segoe UI", 9),
        ).pack(side="right", padx=18)

        # Thin accent line under header
        accent = tk.Frame(self, bg=LINE, height=1)
        accent.pack(fill="x")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        self.tab_dashboard = ttk.Frame(notebook)
        self.tab_connection = ttk.Frame(notebook)
        self.tab_audio = ttk.Frame(notebook)
        self.tab_recording = ttk.Frame(notebook)
        self.tab_nowplaying = ttk.Frame(notebook)
        self.tab_library = ttk.Frame(notebook)
        notebook.add(self.tab_dashboard, text="Dashboard")
        notebook.add(self.tab_connection, text="Connection")
        notebook.add(self.tab_audio, text="Audio")
        notebook.add(self.tab_recording, text="Recording")
        notebook.add(self.tab_nowplaying, text="Now Playing")
        self.tab_automation = ttk.Frame(notebook)
        notebook.add(self.tab_library, text="Library & Playlist")
        notebook.add(self.tab_automation, text="Automation")

        self._build_dashboard_tab()
        self._build_connection_tab()
        self._build_audio_tab()
        self._build_recording_tab()
        self._build_nowplaying_tab()
        self._build_library_tab()
        self._build_automation_tab()

        # ── Status bar (always visible) ──────────────────────────────────
        status_outer = tk.Frame(self, bg=PANEL_RAISED, height=46)
        status_outer.pack(fill="x", padx=0, pady=(10, 0))
        status_outer.pack_propagate(False)

        status_frame = tk.Frame(status_outer, bg=PANEL_RAISED)
        status_frame.pack(fill="both", expand=True, padx=14, pady=8)

        self.status_dot = tk.Canvas(
            status_frame, width=16, height=16, highlightthickness=0, bg=PANEL_RAISED
        )
        self.status_dot.pack(side="left", padx=(0, 8))
        self._dot_id = self.status_dot.create_oval(
            2, 2, 14, 14, fill=STATE_COLORS[StreamState.STOPPED], outline=""
        )

        self.status_label = tk.Label(
            status_frame, text="Stopped", bg=PANEL_RAISED, fg=TEXT,
            font=FONT_UI_SEMI,
        )
        self.status_label.pack(side="left")

        # Live stats from ffmpeg progress
        self.stats_label = tk.Label(
            status_frame, text="", bg=PANEL_RAISED, fg=TEXT_DIM, font=FONT_MONO
        )
        self.stats_label.pack(side="left", padx=(12, 0))

        self.connect_btn = ttk.Button(
            status_frame, text="Go Live", command=self._toggle_stream, style="Accent.TButton"
        )
        self.connect_btn.pack(side="right")

        # ── Connection log ───────────────────────────────────────────────
        log_frame = ttk.LabelFrame(self, text="Connection log")
        log_frame.pack(fill="both", expand=False, padx=12, pady=(8, 12))
        self.log_widget = scrolledtext.ScrolledText(
            log_frame,
            height=7,
            state="disabled",
            wrap="word",
            bg=BG_ALT,
            fg=TEXT_DIM,
            insertbackground=TEXT,
            selectbackground=AMBER,
            selectforeground="#0b0c0e",
            relief="flat",
            borderwidth=0,
            font=FONT_MONO,
            padx=8,
            pady=6,
        )
        self.log_widget.pack(fill="both", expand=True, padx=2, pady=2)

    def _build_dashboard_tab(self) -> None:
        f = self.tab_dashboard
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(4, weight=1)

        # Hero on-air strip
        hero = tk.Frame(f, bg=PANEL_RAISED, highlightthickness=1, highlightbackground=LINE)
        hero.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(14, 10))
        hero.columnconfigure(1, weight=1)

        self.dashboard_live_dot = tk.Canvas(
            hero, width=26, height=26, bg=PANEL_RAISED, highlightthickness=0
        )
        self.dashboard_live_dot.grid(row=0, column=0, padx=(18, 12), pady=16)
        # Soft outer ring + solid core for a more premium indicator
        self.dashboard_live_dot.create_oval(1, 1, 25, 25, outline=LINE, width=1)
        self._dashboard_dot = self.dashboard_live_dot.create_oval(
            5, 5, 21, 21, fill=TEXT_FAINT, outline=""
        )

        self.dashboard_state = tk.Label(
            hero, text="OFF AIR", bg=PANEL_RAISED, fg=TEXT, font=FONT_UI_HERO
        )
        self.dashboard_state.grid(row=0, column=1, sticky="w", pady=12)

        self.dashboard_clock = tk.Label(
            hero, text="00:00:00", bg=PANEL_RAISED, fg=AMBER, font=FONT_MONO_LG
        )
        self.dashboard_clock.grid(row=0, column=2, padx=(12, 20), pady=12)

        # Metric cards
        self.dashboard_cards = []
        cards = [("SERVER", "—"), ("BITRATE", "—"), ("CODEC", "—"), ("RECORDING", "OFF")]
        for i, (title, value) in enumerate(cards):
            card = tk.Frame(f, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
            card.grid(
                row=1, column=i % 2, sticky="ew",
                padx=(12 if i % 2 == 0 else 6, 6 if i % 2 == 0 else 12),
                pady=5,
            )
            tk.Label(
                card, text=title, bg=PANEL, fg=TEXT_FAINT,
                font=("Segoe UI Semibold", 8),
            ).pack(anchor="w", padx=14, pady=(12, 2))
            val = tk.Label(
                card, text=value, bg=PANEL, fg=TEXT, font=FONT_UI_LG
            )
            val.pack(anchor="w", padx=14, pady=(0, 12))
            self.dashboard_cards.append(val)

        # Quick actions
        quick = ttk.LabelFrame(f, text="Quick actions")
        quick.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 8))
        ttk.Button(
            quick, text="Go Live / Stop", command=self._toggle_stream, style="Accent.TButton"
        ).pack(side="left", padx=(10, 6), pady=10)
        ttk.Button(
            quick, text="Test Connection", command=self._test_connection, style="Ghost.TButton"
        ).pack(side="left", padx=4, pady=10)
        ttk.Button(
            quick, text="Start / Stop Recording", command=self._toggle_recording, style="Ghost.TButton"
        ).pack(side="left", padx=4, pady=10)
        ttk.Button(
            quick, text="Refresh Devices", command=self._refresh_devices, style="Ghost.TButton"
        ).pack(side="left", padx=4, pady=10)

        self.dashboard_hint = tk.Label(
            f,
            text="Ready. Configure your station, select an audio source, then go live.",
            bg=BG, fg=TEXT_DIM, anchor="w", justify="left", font=FONT_UI,
        )
        self.dashboard_hint.grid(row=3, column=0, columnspan=2, sticky="nw", padx=16, pady=(6, 12))

    def _build_connection_tab(self) -> None:
        f = self.tab_connection
        for i in range(2):
            f.columnconfigure(i, weight=1)

        row = 0
        ttk.Label(f, text="Server type").grid(row=row, column=0, sticky="w", padx=10, pady=(14, 4))
        self.protocol_var = tk.StringVar()
        protocol_combo = ttk.Combobox(
            f, textvariable=self.protocol_var, state="readonly",
            values=["icecast2", "shoutcast2", "shoutcast1 (legacy)"],
        )
        protocol_combo.grid(row=row, column=1, sticky="ew", padx=10, pady=(14, 4))
        row += 1

        ttk.Label(f, text="Host").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.host_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.host_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        ttk.Label(f, text="Port").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.port_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.port_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        ttk.Label(f, text="Mount point").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.mount_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.mount_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        ttk.Label(f, text="Source username").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.username_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.username_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        ttk.Label(f, text="Password").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.password_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.password_var, show="•").grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        self.tls_var = tk.BooleanVar()
        ttk.Checkbutton(f, text="Use TLS (https source, if server supports it)", variable=self.tls_var).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 4)
        )
        row += 1

        test_row = ttk.Frame(f)
        test_row.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 4))
        test_row.columnconfigure(1, weight=1)
        self.test_conn_btn = ttk.Button(test_row, text="Test Connection", command=self._test_connection)
        self.test_conn_btn.grid(row=0, column=0, sticky="w")
        self.test_conn_label = ttk.Label(test_row, text="", style="Dim.TLabel")
        self.test_conn_label.grid(row=0, column=1, sticky="w", padx=(10, 0))
        row += 1

        note = ttk.Label(
            f,
            text=(
                "Test Connection only checks that the host/port is reachable —\n"
                "it doesn't verify the source password. Shoutcast v1 (legacy) uses\n"
                "a hand-rolled socket handshake for old DNAS servers and is\n"
                "best-effort — test against your actual server."
            ),
            foreground=TEXT_FAINT,
            justify="left",
        )
        note.grid(row=row, column=0, columnspan=2, sticky="w", padx=10, pady=(10, 4))

    def _build_audio_tab(self) -> None:
        f = self.tab_audio
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        self.mixer_rows: dict[str, dict] = {}

        ttk.Label(
            f,
            text="Each connected source gets its own fader — mic, playback/loopback\n"
                 "device (e.g. VB-Audio Virtual Cable, VoiceMeeter, Stereo Mix), or any\n"
                 "other detected device. Enable the ones you want mixed into the stream.",
            foreground=TEXT_FAINT,
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(14, 6))

        mixer_outer = ttk.LabelFrame(f, text="Audio mixer")
        mixer_outer.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 6))
        mixer_outer.columnconfigure(0, weight=1)
        mixer_outer.rowconfigure(0, weight=1)

        # Mixer rows live inside a scrollable canvas rather than growing the
        # tab unbounded — with a handful of DirectShow devices detected,
        # the row list would otherwise run off the bottom of the window
        # with no way to reach the rest.
        mixer_canvas = tk.Canvas(mixer_outer, bg=BG, highlightthickness=0, height=190)
        mixer_canvas.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        mixer_scroll = ttk.Scrollbar(mixer_outer, orient="vertical", command=mixer_canvas.yview)
        mixer_scroll.grid(row=0, column=1, sticky="ns", pady=4)
        mixer_canvas.configure(yscrollcommand=mixer_scroll.set)

        mixer_frame = ttk.Frame(mixer_canvas)
        mixer_window = mixer_canvas.create_window((0, 0), window=mixer_frame, anchor="nw")
        mixer_frame.columnconfigure(0, weight=1)
        self.mixer_frame = mixer_frame

        def _on_mixer_configure(_event=None) -> None:
            mixer_canvas.configure(scrollregion=mixer_canvas.bbox("all"))

        def _on_canvas_configure(event) -> None:
            mixer_canvas.itemconfig(mixer_window, width=event.width)

        def _on_mousewheel(event) -> None:
            mixer_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        mixer_frame.bind("<Configure>", _on_mixer_configure)
        mixer_canvas.bind("<Configure>", _on_canvas_configure)
        # Only capture the scroll wheel while the pointer is actually over
        # the mixer, so hovering it doesn't steal scrolling from the rest
        # of the window.
        mixer_canvas.bind("<Enter>", lambda e: mixer_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        mixer_canvas.bind("<Leave>", lambda e: mixer_canvas.unbind_all("<MouseWheel>"))

        ttk.Button(f, text="Refresh devices", command=self._refresh_devices).grid(
            row=2, column=0, sticky="e", padx=10, pady=(0, 10)
        )

        enc_frame = ttk.LabelFrame(f, text="Encoding")
        enc_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        enc_frame.columnconfigure(1, weight=1)

        erow = 0
        ttk.Label(enc_frame, text="Codec").grid(row=erow, column=0, sticky="w", padx=10, pady=(10, 4))
        self.codec_var = tk.StringVar()
        ttk.Combobox(
            enc_frame, textvariable=self.codec_var, state="readonly", values=["mp3", "aac", "ogg"]
        ).grid(row=erow, column=1, sticky="ew", padx=10, pady=(10, 4))
        erow += 1

        ttk.Label(enc_frame, text="Bitrate (kbps)").grid(row=erow, column=0, sticky="w", padx=10, pady=4)
        self.bitrate_var = tk.StringVar()
        ttk.Combobox(
            enc_frame, textvariable=self.bitrate_var, state="readonly",
            values=["64", "96", "128", "160", "192", "256", "320"],
        ).grid(row=erow, column=1, sticky="ew", padx=10, pady=4)
        erow += 1

        ttk.Label(enc_frame, text="Sample rate (Hz)").grid(row=erow, column=0, sticky="w", padx=10, pady=4)
        self.samplerate_var = tk.StringVar()
        ttk.Combobox(
            enc_frame, textvariable=self.samplerate_var, state="readonly",
            values=["22050", "44100", "48000"],
        ).grid(row=erow, column=1, sticky="ew", padx=10, pady=4)
        erow += 1

        ttk.Label(enc_frame, text="Channels").grid(row=erow, column=0, sticky="w", padx=10, pady=(4, 10))
        self.channels_var = tk.StringVar()
        ttk.Combobox(
            enc_frame, textvariable=self.channels_var, state="readonly", values=["1 (mono)", "2 (stereo)"],
        ).grid(row=erow, column=1, sticky="ew", padx=10, pady=(4, 10))

        self._refresh_devices()

    # ------------------------------------------------------------------
    # Mixer rows (one per audio source: mic, playback device, or other)
    # ------------------------------------------------------------------

    def _rebuild_mixer_rows(self) -> None:
        for child in self.mixer_frame.winfo_children():
            child.destroy()
        self.mixer_rows = {}

        channels = self.config_obj.audio.mixer_channels
        if not channels:
            ttk.Label(
                self.mixer_frame,
                text="No audio sources detected yet — click Refresh devices.",
                foreground=TEXT_FAINT,
            ).grid(row=0, column=0, sticky="w", padx=10, pady=10)
            return

        for i, ch in enumerate(channels):
            row = ttk.Frame(self.mixer_frame)
            row.grid(row=i, column=0, sticky="ew", padx=8, pady=4)
            row.columnconfigure(2, weight=1)

            enabled_var = tk.BooleanVar(value=ch.enabled)
            ttk.Checkbutton(row, variable=enabled_var).grid(row=0, column=0, padx=(2, 6))

            label_var = tk.StringVar(value=ch.label or default_label_for(ch.device))
            ttk.Entry(row, textvariable=label_var, width=16).grid(row=0, column=1, sticky="w")

            pct_label = ttk.Label(row, text=f"{round(ch.volume * 100)}%", width=5, anchor="e")

            def _on_move(value: str, pct_label=pct_label) -> None:
                pct_label.config(text=f"{int(float(value))}%")

            scale = ttk.Scale(row, from_=0, to=150, orient="horizontal", command=_on_move)
            scale.set(round(ch.volume * 100))
            scale.grid(row=0, column=2, sticky="ew", padx=8)

            pct_label.grid(row=0, column=3, padx=(0, 8))

            mute_var = tk.BooleanVar(value=ch.muted)
            ttk.Checkbutton(row, text="Mute", variable=mute_var).grid(row=0, column=4, padx=(0, 4))

            ttk.Label(row, text=ch.device, foreground=TEXT_FAINT).grid(
                row=1, column=0, columnspan=5, sticky="w", padx=(28, 0)
            )

            self.mixer_rows[ch.device] = {
                "enabled": enabled_var,
                "label": label_var,
                "scale": scale,
                "mute": mute_var,
            }

    def _pull_mixer_from_widgets(self) -> None:
        for ch in self.config_obj.audio.mixer_channels:
            widgets = self.mixer_rows.get(ch.device)
            if not widgets:
                continue
            ch.enabled = widgets["enabled"].get()
            ch.label = widgets["label"].get().strip() or default_label_for(ch.device)
            ch.volume = max(0.0, widgets["scale"].get() / 100.0)
            ch.muted = widgets["mute"].get()

    def _build_recording_tab(self) -> None:
        f = self.tab_recording
        f.columnconfigure(1, weight=1)

        ttk.Label(
            f,
            text="Record the same mixer sources straight to a file on this PC —\n"
                 "independent of streaming, so you can record with or without\n"
                 "going live.",
            foreground=TEXT_FAINT,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(14, 10))

        row = 1
        ttk.Label(f, text="Save to").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.record_dir_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.record_dir_var).grid(row=row, column=1, sticky="ew", padx=(10, 4), pady=4)
        ttk.Button(f, text="Browse…", command=self._browse_record_dir).grid(
            row=row, column=2, sticky="w", padx=(0, 10), pady=4
        )
        row += 1

        ttk.Label(f, text="File format").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.record_format_var = tk.StringVar()
        ttk.Combobox(
            f, textvariable=self.record_format_var, state="readonly", values=["mp3", "wav"],
        ).grid(row=row, column=1, sticky="w", padx=10, pady=4)
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=10)
        row += 1

        controls = ttk.Frame(f)
        controls.grid(row=row, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 4))
        controls.columnconfigure(1, weight=1)

        self.record_dot = tk.Canvas(controls, width=14, height=14, highlightthickness=0, bg=BG)
        self.record_dot.grid(row=0, column=0, padx=(0, 8))
        self._record_dot_id = self.record_dot.create_oval(2, 2, 12, 12, fill=TEXT_FAINT)

        self.record_status_label = ttk.Label(controls, text="Not recording")
        self.record_status_label.grid(row=0, column=1, sticky="w")

        self.record_btn = ttk.Button(
            controls, text="Start Recording", command=self._toggle_recording, style="Accent.TButton"
        )
        self.record_btn.grid(row=0, column=2, sticky="e")
        row += 1

        self.record_file_label = ttk.Label(f, text="", foreground=TEXT_FAINT, wraplength=440, justify="left")
        self.record_file_label.grid(row=row, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 10))

    def _build_library_tab(self) -> None:
        f = self.tab_library
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(1, weight=1)
        f.rowconfigure(4, weight=1)

        ttk.Label(f, text="Music Library", style="Header.TLabel").grid(row=0, column=0, sticky="w", padx=10, pady=(12, 6))
        ttk.Label(f, text="Playlist", style="Header.TLabel").grid(row=0, column=1, sticky="w", padx=10, pady=(12, 6))

        left = tk.Frame(f, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=4)
        right = tk.Frame(f, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        right.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=4)

        self.library_list = tk.Listbox(left, bg=BG_ALT, fg=TEXT, selectbackground=AMBER, selectforeground="#0b0c0e", relief="flat", activestyle="none")
        self.library_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.playlist_list = tk.Listbox(right, bg=BG_ALT, fg=TEXT, selectbackground=AMBER, selectforeground="#0b0c0e", relief="flat", activestyle="none")
        self.playlist_list.pack(fill="both", expand=True, padx=4, pady=4)

        controls = ttk.Frame(f)
        controls.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        ttk.Button(controls, text="Add Folder", command=self._library_add_folder).pack(side="left")
        ttk.Button(controls, text="Refresh", command=self._refresh_library_lists).pack(side="left", padx=6)
        ttk.Button(controls, text="Metadata Manager", command=self._library_metadata_manager).pack(side="left", padx=6)
        ttk.Button(controls, text="Add Selected →", command=self._playlist_add_selected).pack(side="left")
        ttk.Button(controls, text="← Remove", command=self._playlist_remove_selected).pack(side="left", padx=6)
        ttk.Button(controls, text="Clear Playlist", command=self._playlist_clear).pack(side="left")
        ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=10, pady=4)
        ttk.Button(controls, text="Previous", command=self._player_previous).pack(side="left")
        self.player_btn = ttk.Button(controls, text="Play Playlist", command=self._player_toggle, style="Accent.TButton")
        self.player_btn.pack(side="left", padx=6)
        ttk.Button(controls, text="Next", command=self._player_next).pack(side="left")
        ttk.Button(controls, text="Stop", command=self._player_stop).pack(side="left", padx=6)

        self.player_status = ttk.Label(f, text="Player stopped", style="Dim.TLabel")
        self.player_status.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(2, 4))
        self.library_status = ttk.Label(f, text="0 tracks · 0 playlist items", style="Dim.TLabel")
        self.library_status.grid(row=4, column=0, columnspan=2, sticky="sw", padx=10, pady=(4, 10))
        self._refresh_library_lists()

    def _library_add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Select music folder")
        if not folder:
            return
        self.library_status.config(text="Scanning music folder…")
        def worker():
            added = self.library.scan([folder])
            self.after(0, lambda: (self._refresh_library_lists(), self.library_status.config(text=f"{len(self.library.tracks)} tracks · {len(self.library.playlist)} playlist items · {added} added")))
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_library_lists(self) -> None:
        if not hasattr(self, "library_list"):
            return
        self.library_list.delete(0, "end")
        for t in self.library.tracks:
            label = f"{t.artist} — {t.title}" if t.artist else t.title
            self.library_list.insert("end", label)
        by_path = {t.path: t for t in self.library.tracks}
        self.playlist_list.delete(0, "end")
        for path in self.library.playlist:
            t = by_path.get(path)
            if t:
                self.playlist_list.insert("end", f"{t.artist} — {t.title}" if t.artist else t.title)
        self.library_status.config(text=f"{len(self.library.tracks)} tracks · {len(self.library.playlist)} playlist items")
        self.player.set_playlist(self.library.playlist)

    def _library_metadata_manager(self) -> None:
        """1.11.0: inspect and edit broadcast metadata/programming fields."""
        win=tk.Toplevel(self); win.title(f"{APP_TITLE} — Intelligent Music Library"); win.geometry("980x620"); win.configure(bg=BG); win.transient(self)
        ttk.Label(win,text="Intelligent Music Library & Metadata",font=("Segoe UI",14,"bold")).pack(anchor="w",padx=14,pady=(14,4))
        ttk.Label(win,text="Tags are imported when available. Energy, mood, language, gender, category and intro/outro are operator-controlled programming metadata.",style="Dim.TLabel").pack(anchor="w",padx=14,pady=(0,10))
        frame=ttk.Frame(win); frame.pack(fill="both",expand=True,padx=12,pady=6)
        cols=("artist","title","album","genre","year","bpm","category","energy","mood")
        tree=ttk.Treeview(frame,columns=cols,show="headings",selectmode="browse")
        widths={"artist":150,"title":190,"album":130,"genre":100,"year":55,"bpm":55,"category":95,"energy":80,"mood":90}
        for c in cols: tree.heading(c,text=c.title()); tree.column(c,width=widths[c],anchor="w")
        tree.pack(side="left",fill="both",expand=True); sb=ttk.Scrollbar(frame,command=tree.yview); sb.pack(side="right",fill="y"); tree.configure(yscrollcommand=sb.set)
        def refresh():
            tree.delete(*tree.get_children())
            for i,t in enumerate(self.library.tracks): tree.insert("","end",iid=str(i),values=(t.artist,t.title,t.album,t.genre,t.year,f"{t.bpm:.0f}" if t.bpm else "",t.category,t.energy,t.mood))
        def edit(_=None):
            sel=tree.selection()
            if not sel: return
            i=int(sel[0]); t=self.library.tracks[i]
            e=tk.Toplevel(win); e.title("Edit Track Metadata"); e.geometry("440x430"); e.transient(win)
            fields=[("Artist","artist"),("Title","title"),("Album","album"),("Genre","genre"),("Year","year"),("BPM","bpm"),("Category","category"),("Energy","energy"),("Mood","mood"),("Language","language"),("Gender","gender"),("Intro seconds","intro"),("Outro seconds","outro")]
            vars={}
            for r,(label,key) in enumerate(fields):
                ttk.Label(e,text=label).grid(row=r,column=0,sticky="w",padx=10,pady=4); v=tk.StringVar(value=str(getattr(t,key,""))); vars[key]=v; ttk.Entry(e,textvariable=v,width=35).grid(row=r,column=1,padx=10,pady=4)
            def save():
                for key in vars:
                    val=vars[key].get().strip()
                    if key in ("year",):
                        try: val=int(val or 0)
                        except: val=0
                    elif key in ("bpm","intro","outro"):
                        try: val=float(val or 0)
                        except: val=0.0
                    setattr(t,key,val)
                self.library.save(); refresh(); self._refresh_library_lists(); e.destroy()
            ttk.Button(e,text="Save Metadata",command=save).grid(row=len(fields),column=1,sticky="e",padx=10,pady=10)
        buttons=ttk.Frame(win); buttons.pack(fill="x",padx=12,pady=10)
        ttk.Button(buttons,text="Refresh Tags from Files",command=lambda: (self.library.enrich_metadata(),refresh(),self._refresh_library_lists())).pack(side="left")
        ttk.Button(buttons,text="Edit Selected",command=edit).pack(side="left",padx=8)
        ttk.Button(buttons,text="Close",command=win.destroy).pack(side="right")
        tree.bind("<Double-1>",edit); refresh()

    def _playlist_add_selected(self) -> None:
        indexes = self.library_list.curselection()
        paths = [self.library.tracks[i].path for i in indexes if i < len(self.library.tracks)]
        self.library.add_to_playlist(paths)
        self._refresh_library_lists()

    def _playlist_remove_selected(self) -> None:
        indexes = self.playlist_list.curselection()
        paths = [self.library.playlist[i] for i in indexes if i < len(self.library.playlist)]
        self.library.remove_from_playlist(paths)
        self._refresh_library_lists()

    def _playlist_clear(self) -> None:
        self.library.playlist = []
        self.library.save()
        self._refresh_library_lists()

    def _on_player_track(self, path: str, ended: bool) -> None:
        name = __import__("pathlib").Path(path).stem if path else "—"
        def apply():
            self.player_status.config(text=("Playing: " + name) if not ended else ("Finished: " + name))
            self.song_var.set(name)
            self.config_obj.metadata.current_song = name
            self.config_obj.save()
            if self.config_obj.connection.host and not ended:
                # Keep server metadata aligned with local automation.
                threading.Thread(target=lambda: self.streamer.push_metadata(name), daemon=True).start()
        self.after(0, apply)

    def _player_toggle(self) -> None:
        if self.automation_store.config.enabled and self.automation_store.config.live_takeover:
            self._automation_manual_takeover = True
            self._automation_owned = False
            self.player.set_auto_advance(True)
            self._automation_log('Presenter takeover: manual playlist control is active.')
        self._pull_from_widgets()
        self.config_obj.save()
        self.player.set_playlist(self.library.playlist)
        if self.player.is_playing():
            self.player.pause()
            self.player_btn.config(text="Resume")
            self.player_status.config(text="Player paused")
        else:
            ok = self.player.play()
            if ok:
                self.player_btn.config(text="Pause")
            else:
                messagebox.showwarning(APP_TITLE, "Add at least one valid track to the playlist, and ensure ffplay is available.")

    def _player_previous(self) -> None:
        self.player.set_playlist(self.library.playlist)
        if self.player.previous():
            self.player_btn.config(text="Pause")

    def _player_next(self) -> None:
        self.player.set_playlist(self.library.playlist)
        if self.player.next():
            self.player_btn.config(text="Pause")

    def _player_stop(self) -> None:
        self._automation_manual_takeover = True
        self._automation_owned = False
        self.player.set_auto_advance(True)
        self.player.stop()
        self.player_btn.config(text="Play Playlist")
        self.player_status.config(text="Player stopped")

    # ------------------------------------------------------------------
    # Radio Automation Scheduler (1.4.0)
    # ------------------------------------------------------------------

    def _build_automation_tab(self) -> None:
        f = self.tab_automation
        f.columnconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)
        f.rowconfigure(3, weight=1)
        f.rowconfigure(4, weight=1)

        top = tk.Frame(f, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))
        self.auto_enabled_var = tk.BooleanVar(value=self.automation_store.config.enabled)
        self.auto_takeover_var = tk.BooleanVar(value=self.automation_store.config.live_takeover)
        ttk.Checkbutton(top, text="Enable 24/7 AutoDJ", variable=self.auto_enabled_var,
                        command=self._automation_settings_changed).pack(side="left", padx=10, pady=8)
        ttk.Checkbutton(top, text="Presenter takeover", variable=self.auto_takeover_var,
                        command=self._automation_settings_changed).pack(side="left", padx=10)
        self.auto_status = ttk.Label(top, text="Automation stopped", style="Dim.TLabel")
        self.auto_status.pack(side="right", padx=10)

        # Program/content assignment
        pframe = ttk.LabelFrame(f, text="Program & content")
        pframe.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=5)
        pframe.columnconfigure(1, weight=1)
        ttk.Label(pframe, text="Program").grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self.auto_program_var = tk.StringVar(value=self.automation_store.config.default_program)
        self.auto_program_combo = ttk.Combobox(pframe, textvariable=self.auto_program_var,
                                                state="readonly",
                                                values=[p.name for p in self.automation_store.config.programs])
        self.auto_program_combo.grid(row=0, column=1, padx=6, pady=6, sticky="ew")
        ttk.Button(pframe, text="New Program", command=self._automation_new_program).grid(row=0, column=2, padx=6)
        ttk.Label(pframe, text="Select tracks in Library & Playlist, then assign them:").grid(row=1, column=0, columnspan=3, sticky="w", padx=6)
        for i, cat in enumerate(("music", "jingle", "commercial", "station_id", "sweeper", "promo", "news")):
            ttk.Button(pframe, text=cat.replace("_", " ").title(),
                       command=lambda c=cat: self._automation_assign(c)).grid(row=2 + i//3, column=i%3, padx=4, pady=4, sticky="ew")
        for i, cat in enumerate(MUSIC_CATEGORIES):
            ttk.Button(pframe, text="♫ " + cat.replace("_", " ").title(),
                       command=lambda c=cat: self._automation_assign(c)).grid(row=4 + i//4, column=i%4, padx=3, pady=3, sticky="ew")
        ttk.Button(pframe, text="Music Rotation", command=self._automation_rotation).grid(row=6, column=0, padx=6, pady=5, sticky="ew")
        ttk.Button(pframe, text="Advanced Music Scheduler", command=self._automation_music_scheduler).grid(row=7, column=0, padx=6, pady=5, sticky="ew")
        ttk.Button(pframe, text="Commercial As-Run Log", command=self._automation_asrun).grid(row=7, column=1, padx=6, pady=5, sticky="ew")
        ttk.Button(pframe, text="Rotation History", command=self._automation_history).grid(row=6, column=1, padx=6, pady=5, sticky="ew")
        ttk.Button(pframe, text="Station Imaging Rules", command=self._automation_imaging_rules).grid(row=6, column=2, padx=6, pady=5, sticky="ew")
        ttk.Button(pframe, text="Broadcast Clock Editor", command=self._automation_clock_editor).grid(row=6, column=3, padx=6, pady=5, sticky="ew")
        ttk.Button(pframe, text="Save Program", command=self._automation_save_program).grid(row=8, column=0, columnspan=4, padx=6, pady=5, sticky="ew")

        # Schedule
        sframe = ttk.LabelFrame(f, text="Clock → Schedule")
        sframe.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=5)
        for i in range(4): sframe.columnconfigure(i, weight=1)
        self.auto_day = ttk.Combobox(sframe, state="readonly",
                                     values=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], width=6)
        self.auto_day.current(0); self.auto_day.grid(row=0, column=0, padx=4, pady=6)
        self.auto_start = ttk.Entry(sframe, width=7); self.auto_start.insert(0, "00:00"); self.auto_start.grid(row=0,column=1,padx=4)
        self.auto_end = ttk.Entry(sframe, width=7); self.auto_end.insert(0, "23:59"); self.auto_end.grid(row=0,column=2,padx=4)
        ttk.Button(sframe, text="Add", command=self._automation_add_schedule).grid(row=0,column=3,padx=4)
        self.schedule_list = tk.Listbox(sframe, bg=BG_ALT, fg=TEXT, selectbackground=AMBER,
                                        selectforeground="#0b0c0e", relief="flat", activestyle="none")
        self.schedule_list.grid(row=1, column=0, columnspan=4, sticky="nsew", padx=4, pady=4)
        sframe.rowconfigure(1, weight=1)
        ttk.Button(sframe, text="Remove Selected", command=self._automation_remove_schedule).grid(row=2,column=0,columnspan=4,pady=5)

        clock = ttk.LabelFrame(f, text="Professional Radio Clock")
        clock.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        self.auto_clock_var = tk.StringVar(value="00:00:00")
        self.auto_clock_name_var = tk.StringVar(value="Overnight Clock")
        self.auto_next_var = tk.StringVar(value="Next: —")
        self.auto_countdown_var = tk.StringVar(value="00:00")
        ttk.Label(clock, textvariable=self.auto_clock_var, font=("Consolas", 16, "bold")).pack(side="left", padx=10, pady=7)
        ttk.Label(clock, textvariable=self.auto_clock_name_var, style="Dim.TLabel").pack(side="left", padx=(0, 10))
        ttk.Label(clock, textvariable=self.auto_next_var, style="Dim.TLabel").pack(side="left", padx=8)
        ttk.Label(clock, textvariable=self.auto_countdown_var, font=("Consolas", 10, "bold")).pack(side="left", padx=8)
        ttk.Button(clock, text="Refresh Preview", command=self._refresh_automation_preview).pack(side="right", padx=6)
        ttk.Button(clock, text="LIVE TAKEOVER", command=self._automation_live_takeover, style="Accent.TButton").pack(side="right", padx=6)
        ttk.Button(clock, text="Play Next", command=self._automation_manual_next).pack(side="right", padx=6)
        ttk.Button(clock, text="Emergency Stop", command=self._automation_emergency_stop).pack(side="right", padx=6)
        ttk.Button(clock, text="Return to AutoDJ", command=self._automation_return_to_auto).pack(side="right", padx=6)
        ttk.Button(clock, text="Commercial Campaigns", command=self._automation_campaigns).pack(side="right", padx=6)
        rules = self.automation_store.config.clock_rules
        self.auto_commercial_every = tk.IntVar(value=rules.commercial_every)
        self.auto_commercial_spots = tk.IntVar(value=rules.commercial_spots)
        self.auto_station_every = tk.IntVar(value=rules.station_id_every)
        ttk.Label(clock, text="Ads every").pack(side="left", padx=(10,2))
        ttk.Spinbox(clock, from_=0, to=60, width=4, textvariable=self.auto_commercial_every, command=self._automation_rules_changed).pack(side="left")
        ttk.Label(clock, text="songs · spots").pack(side="left", padx=(6,2))
        ttk.Spinbox(clock, from_=1, to=10, width=3, textvariable=self.auto_commercial_spots, command=self._automation_rules_changed).pack(side="left")
        ttk.Label(clock, text="· ID every").pack(side="left", padx=(6,2))
        ttk.Spinbox(clock, from_=0, to=60, width=4, textvariable=self.auto_station_every, command=self._automation_rules_changed).pack(side="left")

        info = ttk.Label(f, text="Clock: schedule → program → imaging → music → commercial breaks → station IDs. Empty categories are skipped.", style="Dim.TLabel")
        info.grid(row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(4,2))

        preview_frame = ttk.LabelFrame(f, text="NEXT 10 — Automation Preview")
        preview_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=10, pady=(2,5))
        preview_frame.columnconfigure(1, weight=1)
        self.auto_preview = tk.Listbox(preview_frame, bg=BG_ALT, fg=TEXT, selectbackground=AMBER, selectforeground="#0b0c0e", relief="flat", activestyle="none", height=10)
        self.auto_preview.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)
        preview_frame.rowconfigure(0, weight=1)
        self.auto_log = scrolledtext.ScrolledText(f, height=6, state="disabled", wrap="word",
                                                  bg=BG_ALT, fg=TEXT_DIM, relief="flat", font=("Consolas", 9))
        self.auto_log.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=10, pady=(4,10))
        f.rowconfigure(4, weight=1)
        self._refresh_automation_preview()
        self._refresh_automation_ui()

    def _automation_campaigns(self) -> None:
        win = tk.Toplevel(self)
        win.title(f"{APP_TITLE} — Commercial Campaigns")
        win.geometry("760x500")
        win.configure(bg=BG)
        win.transient(self)
        win.grab_set()
        win.columnconfigure(0, weight=1)
        win.rowconfigure(0, weight=1)
        listbox = tk.Listbox(win, bg=BG_ALT, fg=TEXT, selectbackground=AMBER,
                             selectforeground="#0b0c0e", relief="flat", activestyle="none")
        listbox.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=10, pady=10)

        def refresh():
            listbox.delete(0, "end")
            for c in self.automation_store.config.campaigns:
                status = "ON" if c.enabled else "OFF"
                limits = f"D:{c.daily_limit or '∞'} H:{c.hourly_limit or '∞'}"
                listbox.insert("end", f"[{status}] {c.name} | {len(c.spots)} spots | {limits} | priority {c.priority} | played {c.played_count} | missed {c.missed_count}")

        def add_campaign():
            name = messagebox.askstring(APP_TITLE, "Campaign name:", parent=win)
            if not name: return
            selected = self.library_list.curselection()
            spots = [self.library.tracks[i].path for i in selected if i < len(self.library.tracks)]
            if not spots:
                messagebox.showinfo(APP_TITLE, "Select the advert audio files in Library & Playlist first.", parent=win)
                return
            start = messagebox.askstring(APP_TITLE, "Start date (YYYY-MM-DD), or leave blank:", parent=win) or ""
            end = messagebox.askstring(APP_TITLE, "End date (YYYY-MM-DD), or leave blank:", parent=win) or ""
            try:
                daily = int(messagebox.askstring(APP_TITLE, "Daily play limit (0 = unlimited):", initialvalue="0", parent=win) or "0")
                hourly = int(messagebox.askstring(APP_TITLE, "Hourly play limit (0 = unlimited):", initialvalue="0", parent=win) or "0")
                priority = int(messagebox.askstring(APP_TITLE, "Priority (higher runs first):", initialvalue="0", parent=win) or "0")
                duration = int(messagebox.askstring(APP_TITLE, "Spot duration in seconds:", initialvalue="30", parent=win) or "30")
            except ValueError:
                messagebox.showwarning(APP_TITLE, "Limits, priority and duration must be numbers.", parent=win); return
            self.automation_store.config.campaigns.append(CommercialCampaign(name=name.strip(), spots=spots,
                start_date=start.strip(), end_date=end.strip(), daily_limit=max(0,daily), hourly_limit=max(0,hourly),
                priority=priority, spot_duration=max(1,duration)))
            self.automation_store.save(); refresh(); self._automation_log(f"Campaign created: {name}")

        def remove_campaign():
            sel = listbox.curselection()
            for i in reversed(sel):
                if i < len(self.automation_store.config.campaigns):
                    name = self.automation_store.config.campaigns[i].name
                    self.automation_store.config.campaigns.pop(i)
                    self._automation_log(f"Campaign removed: {name}")
            self.automation_store.save(); refresh()

        def toggle_campaign():
            sel = listbox.curselection()
            for i in sel:
                if i < len(self.automation_store.config.campaigns):
                    c = self.automation_store.config.campaigns[i]; c.enabled = not c.enabled
            self.automation_store.save(); refresh()

        ttk.Button(win, text="Add Campaign", command=add_campaign).grid(row=1,column=0,padx=5,pady=8)
        ttk.Button(win, text="Enable / Disable", command=toggle_campaign).grid(row=1,column=1,padx=5,pady=8)
        ttk.Button(win, text="Remove", command=remove_campaign).grid(row=1,column=2,padx=5,pady=8)
        ttk.Button(win, text="Close", command=win.destroy).grid(row=1,column=3,padx=5,pady=8)
        refresh()

    def _automation_rotation(self) -> None:
        c = self.automation_store.config
        r = c.music_rotation
        win = tk.Toplevel(self); win.title("Music Rotation"); win.geometry("620x330"); win.transient(self)
        ttk.Label(win, text="Professional Music Rotation", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(14,6))
        enabled = tk.BooleanVar(value=r.enabled)
        ttk.Checkbutton(win, text="Enable category-based music rotation", variable=enabled).pack(anchor="w", padx=14, pady=5)
        ttk.Label(win, text="Rotation sequence (comma separated):").pack(anchor="w", padx=14, pady=(12,2))
        seq = tk.StringVar(value=", ".join(r.sequence or DEFAULT_ROTATION))
        ttk.Entry(win, textvariable=seq).pack(fill="x", padx=14)
        ttk.Label(win, text="Available: Power, Current, Recurrent, Gold, Oldies, Slow, Up-tempo", style="Dim.TLabel").pack(anchor="w", padx=14, pady=4)
        row=ttk.Frame(win); row.pack(fill="x", padx=14, pady=12)
        artist=tk.IntVar(value=r.artist_separation); song=tk.IntVar(value=r.song_separation)
        ttk.Label(row,text="Artist separation:").pack(side="left"); ttk.Spinbox(row,from_=0,to=20,width=4,textvariable=artist).pack(side="left",padx=5)
        ttk.Label(row,text="Songs:").pack(side="left",padx=(15,0)); ttk.Spinbox(row,from_=0,to=20,width=4,textvariable=song).pack(side="left",padx=5)
        def save():
            vals=[x.strip().lower().replace("-","_").replace(" ","_") for x in seq.get().split(",") if x.strip()]
            vals=[x for x in vals if x in MUSIC_CATEGORIES]
            c.music_rotation.enabled=bool(enabled.get())
            c.music_rotation.sequence=vals or list(DEFAULT_ROTATION)
            c.music_rotation.artist_separation=max(0,int(artist.get())); c.music_rotation.song_separation=max(0,int(song.get()))
            c.music_rotation=c.music_rotation.normalized(); self.automation_store.save(); self.automation._rotation_index=0
            self._refresh_automation_preview(); self._automation_log("Music rotation saved: " + " → ".join(c.music_rotation.sequence)); win.destroy()
        ttk.Button(row,text="Save Rotation",command=save).pack(side="right")
        ttk.Button(row,text="Close",command=win.destroy).pack(side="right",padx=6)

    def _automation_clock_editor(self) -> None:
        """1.9.0 visual editor for rule-driven hourly broadcast clocks."""
        from scheduler import ClockTemplate, ClockEvent, default_clock_templates
        win = tk.Toplevel(self); win.title(f"{APP_TITLE} — Broadcast Clock Editor"); win.geometry("1120x650"); win.configure(bg=BG); win.transient(self); win.grab_set()
        win.columnconfigure(0, weight=1); win.columnconfigure(1, weight=2); win.rowconfigure(1, weight=1)
        top=ttk.Frame(win,padding=10); top.grid(row=0,column=0,columnspan=2,sticky="ew")
        ttk.Label(top,text="Broadcast Clock Editor",font=("Segoe UI",14,"bold")).pack(side="left")
        ttk.Label(top,text="Build each hour from events — no hard-coded sequence",style="Dim.TLabel").pack(side="left",padx=14)
        left=ttk.LabelFrame(win,text="Clock Templates",padding=8); left.grid(row=1,column=0,sticky="nsew",padx=(10,5),pady=(0,10)); left.rowconfigure(0,weight=1); left.columnconfigure(0,weight=1)
        right=ttk.LabelFrame(win,text="Hourly Events",padding=8); right.grid(row=1,column=1,sticky="nsew",padx=(5,10),pady=(0,10)); right.rowconfigure(1,weight=1); right.columnconfigure(0,weight=1)
        templates=self.automation_store.config.clock_templates; selected_template=[None]
        ttree=ttk.Treeview(left,columns=("name","hours","events"),show="headings",selectmode="browse")
        for c,w in (("name",150),("hours",90),("events",70)): ttree.heading(c,text=c.title()); ttree.column(c,width=w,anchor="w")
        ttree.grid(row=0,column=0,sticky="nsew"); ts=ttk.Scrollbar(left,orient="vertical",command=ttree.yview); ts.grid(row=0,column=1,sticky="ns"); ttree.configure(yscrollcommand=ts.set)
        etree=ttk.Treeview(right,columns=("minute","category","count","label"),show="headings",selectmode="browse")
        for c,w in (("minute",80),("category",130),("count",70),("label",300)): etree.heading(c,text=c.title()); etree.column(c,width=w,anchor="w")
        etree.grid(row=1,column=0,sticky="nsew"); es=ttk.Scrollbar(right,orient="vertical",command=etree.yview); es.grid(row=1,column=1,sticky="ns"); etree.configure(yscrollcommand=es.set)
        def refresh_events():
            etree.delete(*etree.get_children()); t=selected_template[0]
            if t:
                for e in sorted(t.events,key=lambda x:x.minute): etree.insert("","end",values=(f"{e.minute:02d}:00",e.category,e.count,e.label))
        def refresh_templates():
            ttree.delete(*ttree.get_children())
            for t in templates: ttree.insert("","end",values=(t.name,f"{t.start_hour:02d}:00–{t.end_hour:02d}:59",len(t.events)))
        def select_template(_=None):
            sel=ttree.selection()
            if sel:
                i=ttree.index(sel[0]); selected_template[0]=templates[i] if i<len(templates) else None; refresh_events()
        ttree.bind("<<TreeviewSelect>>",select_template)
        def edit_template(existing=None):
            t=existing or ClockTemplate("New Clock",0,23,[]); d=tk.Toplevel(win); d.title("Clock Template"); d.geometry("430x250"); d.transient(win); d.grab_set()
            vals={k:tk.StringVar(value=str(v)) for k,v in {"name":t.name,"start":t.start_hour,"end":t.end_hour}.items()}
            for i,(k,label) in enumerate((("name","Name"),("start","Start hour (0–23)"),("end","End hour (0–23)"))):
                ttk.Label(d,text=label).grid(row=i,column=0,sticky="w",padx=12,pady=10); ttk.Entry(d,textvariable=vals[k],width=24).grid(row=i,column=1,padx=12,pady=10)
            def save_t():
                try:
                    name=vals["name"].get().strip(); st=int(vals["start"].get()); en=int(vals["end"].get())
                    if not name or not 0<=st<=23 or not 0<=en<=23: raise ValueError("Name and hours must be valid")
                    nt=ClockTemplate(name,st,en,list(t.events))
                    if existing is None: templates.append(nt)
                    else: templates[templates.index(existing)]=nt
                    self.automation_store.save(); refresh_templates(); d.destroy()
                except Exception as exc: messagebox.showerror(APP_TITLE,str(exc),parent=d)
            ttk.Button(d,text="Save Clock",command=save_t,style="Accent.TButton").grid(row=4,column=1,sticky="e",padx=12,pady=12)
        def selected_template_obj():
            sel=ttree.selection(); return templates[ttree.index(sel[0])] if sel and ttree.index(sel[0])<len(templates) else None
        def edit_event(existing=None):
            t=selected_template_obj()
            if not t:return
            e=existing or ClockEvent(0,"music",1,""); d=tk.Toplevel(win); d.title("Clock Event"); d.geometry("460x310"); d.transient(win); d.grab_set()
            vals={k:tk.StringVar(value=str(v)) for k,v in {"minute":e.minute,"category":e.category,"count":e.count,"label":e.label}.items()}
            fields=(("minute","Target minute (0–59)"),("category","Category"),("count","Count"),("label","Label"))
            for i,(k,label) in enumerate(fields):
                ttk.Label(d,text=label).grid(row=i,column=0,sticky="w",padx=12,pady=8)
                w=ttk.Combobox(d,textvariable=vals[k],state="readonly",values=["music","power","current","recurrent","gold","oldies","slow","up_tempo","station_id","jingle","sweeper","promo","news","commercial"]) if k=="category" else ttk.Entry(d,textvariable=vals[k],width=28)
                w.grid(row=i,column=1,padx=12,pady=8,sticky="ew")
            def save_e():
                try:
                    ne=ClockEvent(int(vals["minute"].get()),vals["category"].get(),max(1,int(vals["count"].get())),vals["label"].get().strip())
                    if not 0<=ne.minute<=59: raise ValueError("Minute must be 0–59")
                    if existing is None:t.events.append(ne)
                    else:t.events[t.events.index(existing)]=ne
                    t.events.sort(key=lambda x:x.minute); self.automation_store.save(); refresh_templates(); refresh_events(); d.destroy()
                except Exception as exc: messagebox.showerror(APP_TITLE,str(exc),parent=d)
            ttk.Button(d,text="Save Event",command=save_e,style="Accent.TButton").grid(row=5,column=1,sticky="e",padx=12,pady=12)
        def selected_event():
            sel=etree.selection(); t=selected_template_obj()
            if not sel or not t:return None
            evs=sorted(t.events,key=lambda x:x.minute); i=etree.index(sel[0]); return evs[i] if i<len(evs) else None
        lb=ttk.Frame(left); lb.grid(row=1,column=0,columnspan=2,sticky="ew",pady=8)
        ttk.Button(lb,text="New",command=lambda:edit_template()).pack(side="left"); ttk.Button(lb,text="Edit",command=lambda:edit_template(selected_template_obj()) if selected_template_obj() else None).pack(side="left",padx=4)
        def del_t():
            t=selected_template_obj()
            if t and len(templates)>1 and messagebox.askyesno(APP_TITLE,f"Delete clock '{t.name}'?",parent=win): templates.remove(t); self.automation_store.save(); refresh_templates(); refresh_events()
        ttk.Button(lb,text="Delete",command=del_t).pack(side="left"); ttk.Button(lb,text="Restore Defaults",command=lambda:(templates.__setitem__(slice(None),default_clock_templates()),self.automation_store.save(),refresh_templates(),refresh_events())).pack(side="left",padx=8)
        rb=ttk.Frame(right); rb.grid(row=2,column=0,columnspan=2,sticky="ew",pady=8)
        ttk.Label(rb,text="Target minute events; playback is never interrupted by the clock.",style="Dim.TLabel").pack(side="left"); ttk.Button(rb,text="Add Event",command=lambda:edit_event()).pack(side="left",padx=12); ttk.Button(rb,text="Edit Selected",command=lambda:edit_event(selected_event()) if selected_event() else None).pack(side="left")
        def del_e():
            e=selected_event(); t=selected_template_obj()
            if e and t and messagebox.askyesno(APP_TITLE,"Delete selected event?",parent=win): t.events.remove(e); self.automation_store.save(); refresh_templates(); refresh_events()
        ttk.Button(rb,text="Delete Selected",command=del_e).pack(side="left",padx=4); ttk.Button(rb,text="Close",command=win.destroy).pack(side="right")
        refresh_templates()
        if templates: ttree.selection_set(ttree.get_children()[0]); ttree.focus(ttree.get_children()[0]); select_template()

    def _automation_imaging_rules(self) -> None:
        """Edit the declarative 1.7.0 station-imaging rules."""
        from scheduler import ImagingRule
        win = tk.Toplevel(self)
        win.title(f"{APP_TITLE} — Station Imaging Rules")
        win.geometry("980x560")
        win.configure(bg=BG)
        win.transient(self)
        win.grab_set()
        win.columnconfigure(0, weight=1); win.rowconfigure(0, weight=1)

        frame = ttk.Frame(win, padding=10)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)

        cols=("enabled","name","trigger","action","interval","after","dayparts","priority")
        tree=ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        widths={"enabled":65,"name":190,"trigger":120,"action":100,"interval":70,"after":110,"dayparts":170,"priority":70}
        for c in cols:
            tree.heading(c, text=c.title()); tree.column(c, width=widths[c], anchor="w")
        tree.grid(row=0,column=0,sticky="nsew")
        scroll=ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll.grid(row=0,column=1,sticky="ns"); tree.configure(yscrollcommand=scroll.set)

        def refresh():
            tree.delete(*tree.get_children())
            for r in self.automation_store.config.imaging_rules:
                tree.insert("", "end", values=("Yes" if r.enabled else "No", r.name, r.trigger,
                    r.action, r.interval, r.after, ", ".join(r.dayparts) or "All", r.priority))

        def edit_rule(existing=None):
            r = existing or ImagingRule("New Rule", "song_count", "sweeper")
            d=tk.Toplevel(win); d.title("Imaging Rule"); d.geometry("500x430"); d.transient(win); d.grab_set()
            vars={k:tk.StringVar(value=str(v)) for k,v in {
                "name":r.name,"trigger":r.trigger,"action":r.action,"interval":r.interval,
                "after":r.after,"dayparts":",".join(r.dayparts),"priority":r.priority,
                "count":r.count,"cooldown":r.cooldown,"enabled":"Yes" if r.enabled else "No"}.items()}
            labels=[("name","Name"),("trigger","Trigger"),("action","Action"),("interval","Interval"),("after","After category"),("dayparts","Dayparts (comma separated)"),("priority","Priority"),("count","Count"),("cooldown","Cooldown minutes")]
            for i,(key,label) in enumerate(labels):
                ttk.Label(d,text=label).grid(row=i,column=0,sticky="w",padx=12,pady=6)
                if key=="trigger":
                    w=ttk.Combobox(d,textvariable=vars[key],state="readonly",
                        values=["song_count","minute_interval","top_of_hour","after_category","after_power"])
                elif key=="action":
                    w=ttk.Combobox(d,textvariable=vars[key],state="readonly",
                        values=["station_id","jingle","sweeper","promo","news","commercial","music"])
                else:
                    w=ttk.Entry(d,textvariable=vars[key],width=34)
                w.grid(row=i,column=1,sticky="ew",padx=12,pady=6)
            ttk.Checkbutton(d,text="Enabled",variable=vars["enabled"],
                            onvalue="Yes",offvalue="No").grid(row=len(labels),column=1,sticky="w",padx=12,pady=6)
            def save_rule():
                try:
                    nr=ImagingRule(vars["name"].get().strip(),vars["trigger"].get(),vars["action"].get(),
                        int(vars["interval"].get()),vars["after"].get().strip(),
                        [x.strip() for x in vars["dayparts"].get().split(",") if x.strip()],
                        vars["enabled"].get()=="Yes",int(vars["priority"].get()),
                        int(vars["count"].get()),int(vars["cooldown"].get())).normalized()
                    if not nr.name: raise ValueError("Rule name is required")
                    if existing is None: self.automation_store.config.imaging_rules.append(nr)
                    else:
                        i=self.automation_store.config.imaging_rules.index(existing)
                        self.automation_store.config.imaging_rules[i]=nr
                    self.automation_store.save(); refresh(); self._refresh_automation_preview(); d.destroy()
                except Exception as exc:
                    messagebox.showerror(APP_TITLE, f"Invalid rule: {exc}", parent=d)
            ttk.Button(d,text="Save Rule",command=save_rule,style="Accent.TButton").grid(row=len(labels)+1,column=1,sticky="e",padx=12,pady=12)

        def selected():
            sel=tree.selection()
            if not sel: return None
            idx=tree.index(sel[0])
            rules=self.automation_store.config.imaging_rules
            return rules[idx] if idx < len(rules) else None

        buttons=ttk.Frame(frame)
        buttons.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(10,0))
        ttk.Button(buttons,text="Add Rule",command=lambda: edit_rule()).pack(side="left")
        ttk.Button(buttons,text="Edit Selected",command=lambda: edit_rule(selected()) if selected() else None).pack(side="left",padx=6)
        def delete_selected():
            r=selected()
            if r and messagebox.askyesno(APP_TITLE,f"Delete imaging rule '{r.name}'?",parent=win):
                self.automation_store.config.imaging_rules.remove(r)
                self.automation_store.save(); refresh(); self._refresh_automation_preview()
        ttk.Button(buttons,text="Delete Selected",command=delete_selected).pack(side="left")
        ttk.Button(buttons,text="Restore Professional Defaults",command=lambda: (
            self.automation_store.config.__setattr__("imaging_rules", __import__("scheduler").default_imaging_rules()),
            self.automation_store.save(), refresh(), self._refresh_automation_preview()
        )).pack(side="left",padx=12)
        ttk.Button(buttons,text="Close",command=win.destroy).pack(side="right")
        refresh()

    def _automation_music_scheduler(self) -> None:
        """1.10.0 advanced selector constraints, quotas and explainability."""
        from scheduler import MusicSchedulerRules
        c=self.automation_store.config; r=c.music_scheduler.normalized()
        win=tk.Toplevel(self); win.title(f"{APP_TITLE} — Advanced Music Scheduler"); win.geometry("780x520"); win.configure(bg=BG); win.transient(self); win.grab_set()
        ttk.Label(win,text="Advanced Music Scheduler",font=("Segoe UI",14,"bold")).pack(anchor="w",padx=14,pady=(14,4))
        ttk.Label(win,text="Selection is rule-driven: separation → daypart quotas → fallback → explainable reason.",style="Dim.TLabel").pack(anchor="w",padx=14,pady=(0,12))
        enabled=tk.BooleanVar(value=r.enabled); fallback=tk.BooleanVar(value=r.fallback_enabled)
        ttk.Checkbutton(win,text="Enable advanced selector",variable=enabled).pack(anchor="w",padx=14,pady=4)
        ttk.Checkbutton(win,text="Allow graceful fallback when constraints cannot be satisfied",variable=fallback).pack(anchor="w",padx=14,pady=4)
        row=ttk.Frame(win); row.pack(fill="x",padx=14,pady=10)
        vars_=[]
        for label,val in (("Artist separation",r.artist_separation),("Title separation",r.title_separation),("Category separation",r.category_separation),("History size",r.recent_history)):
            ttk.Label(row,text=label).pack(side="left",padx=(0,4)); v=tk.IntVar(value=val); vars_.append(v); ttk.Spinbox(row,from_=0,to=100,width=5,textvariable=v).pack(side="left",padx=(0,12))
        ttk.Label(win,text="Daypart minimum quotas (plays per recent scheduler window)",font=("Segoe UI",10,"bold")).pack(anchor="w",padx=14,pady=(8,4))
        grid=ttk.Frame(win); grid.pack(fill="x",padx=14)
        cats=["power","current","recurrent","gold","oldies","slow","up_tempo"]; quota_vars={}
        for j,cat in enumerate(cats): ttk.Label(grid,text=cat.replace("_"," ").title()).grid(row=0,column=j,padx=3,pady=3)
        for i,dp in enumerate(("Morning","Afternoon","Evening","Overnight"),1):
            ttk.Label(grid,text=dp).grid(row=i,column=0,padx=3,pady=3,sticky="w")
            for j,cat in enumerate(cats,1):
                v=tk.IntVar(value=r.daypart_quotas.get(dp,{}).get(cat,0)); quota_vars[(dp,cat)]=v
                ttk.Spinbox(grid,from_=0,to=20,width=4,textvariable=v).grid(row=i,column=j,padx=2,pady=2)
        def save():
            quotas={dp:{cat:max(0,int(quota_vars[(dp,cat)].get())) for cat in cats if int(quota_vars[(dp,cat)].get())>0} for dp in ("Morning","Afternoon","Evening","Overnight")}
            c.music_scheduler=MusicSchedulerRules(bool(enabled.get()),max(0,int(vars_[0].get())),max(0,int(vars_[1].get())),max(0,int(vars_[2].get())),max(1,int(vars_[3].get())),quotas,bool(fallback.get())).normalized()
            self.automation_store.save(); self.automation._recent_artists=[]; self.automation._recent_songs=[]; self._refresh_automation_preview(); self._automation_log("Advanced Music Scheduler saved."); win.destroy()
        ttk.Button(win,text="Save Scheduler Rules",command=save).pack(side="right",padx=14,pady=14)
        ttk.Button(win,text="Close",command=win.destroy).pack(side="right",pady=14)

    def _automation_history(self) -> None:
        win = tk.Toplevel(self); win.title("Rotation History / Audit Log"); win.geometry("900x430"); win.transient(self)
        ttk.Label(win, text="Music Rotation History", font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(12,6))
        cols=("time","category","artist","title","reason")
        tree=ttk.Treeview(win, columns=cols, show="headings")
        for col, width in (("time",145),("category",95),("artist",170),("title",220),("reason",240)):
            tree.heading(col,text=col.title()); tree.column(col,width=width,anchor="w")
        tree.pack(fill="both", expand=True, padx=12, pady=8)
        for item in reversed(self.automation.rotation_history(100)):
            tree.insert("","end",values=(item.get("time",""),item.get("category",""),item.get("artist",""),item.get("title",""),item.get("reason","")))
        ttk.Button(win,text="Close",command=win.destroy).pack(anchor="e",padx=12,pady=(0,10))

    def _log_commercial_play(self, path: str, program: str) -> None:
        try:
            campaign = asrun.campaign_for_path(self.automation_store.config.campaigns, path)
            asrun.log_play(campaign, path, program)
        except Exception as exc:
            self._automation_log(f"As-run log failed: {exc}")

    def _automation_asrun(self) -> None:
        win = tk.Toplevel(self)
        win.title(f"{APP_TITLE} — Commercial As-Run Log")
        win.geometry("900x500")
        win.configure(bg=BG)
        win.transient(self)

        today = datetime.date.today()
        from_var = tk.StringVar(value=today.isoformat())
        to_var = tk.StringVar(value=today.isoformat())
        camp_var = tk.StringVar(value="All campaigns")
        count_var = tk.StringVar(value="")
        state = {"rows": [], "start": None, "end": None, "campaign": ""}

        top = ttk.Frame(win)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(top, text="From").pack(side="left")
        ttk.Entry(top, textvariable=from_var, width=11).pack(side="left", padx=(4, 10))
        ttk.Label(top, text="To").pack(side="left")
        ttk.Entry(top, textvariable=to_var, width=11).pack(side="left", padx=(4, 10))
        camp_combo = ttk.Combobox(top, textvariable=camp_var, state="readonly", width=24)
        camp_combo.pack(side="left", padx=(0, 10))

        cols = ("date", "time", "campaign", "spot", "program")
        frame = ttk.Frame(win)
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for col, width in (("date", 95), ("time", 80), ("campaign", 210), ("spot", 260), ("program", 200)):
            tree.heading(col, text=col.title())
            tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)

        def parse_range():
            try:
                start = datetime.date.fromisoformat(from_var.get().strip())
                end = datetime.date.fromisoformat(to_var.get().strip())
            except ValueError:
                messagebox.showwarning(APP_TITLE, "Use dates like 2026-09-24 (year-month-day).", parent=win)
                return None
            if end < start:
                messagebox.showwarning(APP_TITLE, "The end date is before the start date.", parent=win)
                return None
            return start, end

        def refresh():
            rng = parse_range()
            if not rng:
                return
            start, end = rng
            names = ["All campaigns"] + asrun.campaign_names()
            camp_combo["values"] = names
            if camp_var.get() not in names:
                camp_var.set("All campaigns")
            campaign = "" if camp_var.get() == "All campaigns" else camp_var.get()
            rows = asrun.read_log(start, end, campaign)
            state.update(rows=rows, start=start, end=end, campaign=campaign)
            tree.delete(*tree.get_children())
            for r in rows:
                tree.insert("", "end", values=(r["date"], r["time"], r["campaign"], r["spot"], r["program"]))
            count_var.set(f"{len(rows)} plays")

        def set_days(days: int):
            t = datetime.date.today()
            from_var.set((t - datetime.timedelta(days=days)).isoformat())
            to_var.set(t.isoformat())
            refresh()

        def print_report():
            refresh()
            if not state["rows"]:
                messagebox.showinfo(APP_TITLE, "There are no commercial plays in this period to print.", parent=win)
                return
            import webbrowser
            station = self.station_var.get().strip() or "My Station"
            path = asrun.write_report(state["rows"], station, state["start"], state["end"], state["campaign"])
            webbrowser.open(path.as_uri())

        def export_csv():
            refresh()
            if not state["rows"]:
                messagebox.showinfo(APP_TITLE, "There are no commercial plays in this period to export.", parent=win)
                return
            dest = filedialog.asksaveasfilename(parent=win, defaultextension=".csv", initialfile="commercial-asrun.csv",
                                                filetypes=[("CSV file", "*.csv")])
            if dest:
                asrun.export_csv(state["rows"], dest)
                messagebox.showinfo(APP_TITLE, f"Saved {len(state['rows'])} plays to:\n{dest}", parent=win)

        ttk.Button(top, text="Today", command=lambda: set_days(0)).pack(side="left", padx=2)
        ttk.Button(top, text="Last 7 days", command=lambda: set_days(6)).pack(side="left", padx=2)
        ttk.Button(top, text="Refresh", command=refresh).pack(side="left", padx=2)

        frame.pack(fill="both", expand=True, padx=12, pady=6)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        bottom = ttk.Frame(win)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Label(bottom, textvariable=count_var).pack(side="left")
        ttk.Button(bottom, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(bottom, text="Export CSV", command=export_csv).pack(side="right", padx=6)
        ttk.Button(bottom, text="Print Report", command=print_report, style="Accent.TButton").pack(side="right")
        camp_combo.bind("<<ComboboxSelected>>", lambda _e: refresh())
        refresh()

    def _automation_rules_changed(self) -> None:
        try:
            r = self.automation_store.config.clock_rules
            r.commercial_every = max(0, int(self.auto_commercial_every.get()))
            r.commercial_spots = max(1, int(self.auto_commercial_spots.get()))
            r.station_id_every = max(0, int(self.auto_station_every.get()))
            self.automation_store.save()
            self._refresh_automation_preview()
        except (tk.TclError, ValueError):
            pass


    def _automation_manual_next(self) -> None:
        if not self.automation_store.config.enabled:
            messagebox.showinfo(APP_TITLE, "Enable 24/7 AutoDJ first.")
            return
        self._automation_manual_takeover = False
        self._automation_owned = False
        self.player.stop()
        self.player.set_auto_advance(False)
        self._automation_next()
        self._automation_log("Manual NEXT — automation advanced to the next clock item.")

    def _automation_emergency_stop(self) -> None:
        self._automation_manual_takeover = True
        self._automation_takeover_active = False
        self._automation_owned = False
        self.player.stop()
        self.player.set_auto_advance(True)
        self.auto_status.config(text="EMERGENCY STOP")
        self._automation_log("EMERGENCY STOP — automation audio stopped.")

    def _automation_live_takeover(self) -> None:
        if not self.automation_store.config.enabled:
            messagebox.showinfo(APP_TITLE, "Enable 24/7 AutoDJ first.")
            return
        if not self.automation_store.config.live_takeover:
            messagebox.showinfo(APP_TITLE, "Presenter takeover is disabled in Automation settings.")
            return
        if self._automation_takeover_active:
            self._automation_log("Presenter takeover is already active.")
            return
        if self.player.begin_takeover():
            self._automation_takeover_active = True
            self._automation_manual_takeover = True
            self._automation_owned = False
            self.auto_status.config(text="LIVE TAKEOVER • AutoDJ PAUSED")
            self._automation_log("LIVE TAKEOVER — AutoDJ paused; current song position preserved.")
        else:
            self._automation_log("LIVE TAKEOVER failed — AutoDJ could not be paused.")

    def _automation_return_to_auto(self) -> None:
        if not self.automation_store.config.enabled:
            messagebox.showinfo(APP_TITLE, "Enable 24/7 AutoDJ first.")
            return
        if self._automation_takeover_active:
            resumed = self.player.end_takeover(resume=True)
            self._automation_takeover_active = False
            self._automation_manual_takeover = False
            self._automation_owned = resumed
            self.player.set_auto_advance(False)
            if resumed:
                self.auto_status.config(text="AUTO • resumed at saved position")
                self._automation_log("RETURN TO AUTODJ — automation resumed at the saved track position.")
            else:
                self._automation_log("RETURN TO AUTODJ — no paused AutoDJ track was available; automation will select the next item.")
                self.after(100, self._automation_next)
        else:
            self._automation_manual_takeover = False
            self._automation_owned = False
            self.player.set_auto_advance(False)
            self._automation_log("Presenter takeover released — AutoDJ control restored.")
            if self.automation.enabled_now() and not self.player.is_playing():
                self._automation_next()
        self._refresh_automation_preview()

    def _refresh_automation_preview(self) -> None:
        if not hasattr(self, "auto_preview"):
            return
        try:
            self.auto_preview.delete(0, "end")
            items = self.automation.preview(10)
            for i, (category, path, program) in enumerate(items, 1):
                from scheduler import track_metadata
                artist, title = track_metadata(path)
                label = category.replace("_", " ").title()
                self.auto_preview.insert("end", f"{i:02d}  {label:12}  {artist} — {title}  [{program}]")
            if items:
                from scheduler import track_metadata
                artist, title = track_metadata(items[0][1])
                self.auto_next_var.set(f"Next: {artist} — {title}")
            else:
                self.auto_next_var.set("Next: —")
        except Exception as exc:
            self.auto_next_var.set(f"Preview error: {exc}")

    def _automation_log(self, text: str) -> None:
        self.auto_log.config(state="normal")
        self.auto_log.insert("end", text + "\n")
        self.auto_log.see("end")
        self.auto_log.config(state="disabled")

    def _automation_settings_changed(self) -> None:
        c = self.automation_store.config
        c.enabled = self.auto_enabled_var.get()
        c.live_takeover = self.auto_takeover_var.get()
        self.automation_store.save()
        if c.enabled:
            self._automation_manual_takeover = False
            self._automation_owned = False
            self.player.set_auto_advance(False)
            self._automation_log("AutoDJ enabled — waiting for schedule/clock.")
        else:
            self._automation_manual_takeover = True
            self.player.set_auto_advance(True)
            self.player.stop()
            self.auto_status.config(text="Automation stopped")

    def _automation_new_program(self) -> None:
        name = messagebox.askstring(APP_TITLE, "Program name:")
        if not name: return
        if any(p.name.lower() == name.lower() for p in self.automation_store.config.programs):
            return
        from scheduler import Program
        self.automation_store.config.programs.append(Program(name))
        self.automation_store.config.default_program = name
        self.automation_store.save()
        self._refresh_automation_ui()

    def _automation_current_program(self):
        name = self.auto_program_var.get()
        for p in self.automation_store.config.programs:
            if p.name == name: return p
        return None

    def _automation_assign(self, category: str) -> None:
        indexes = self.library_list.curselection()
        if not indexes:
            messagebox.showinfo(APP_TITLE, "Select one or more tracks in Library & Playlist first.")
            return
        p = self._automation_current_program()
        if not p: return
        target = getattr(p, category)
        for i in indexes:
            if i < len(self.library.tracks):
                path = self.library.tracks[i].path
                if path not in target: target.append(path)
        self.automation_store.save()
        self._automation_log(f"Assigned {len(indexes)} item(s) to {category}.")
        self._refresh_automation_ui()

    def _automation_save_program(self) -> None:
        p = self._automation_current_program()
        if not p: return
        self.automation_store.config.default_program = p.name
        self.automation_store.save()
        self._automation_log(f"Saved program: {p.name}")
        self.automation.current_program = None
        self._refresh_automation_preview()

    def _automation_add_schedule(self) -> None:
        try:
            day = self.auto_day.current()
            start, end = self.auto_start.get().strip(), self.auto_end.get().strip()
            # Validate clock values without requiring a date.
            datetime.datetime.strptime(start, "%H:%M")
            datetime.datetime.strptime(end, "%H:%M")
        except ValueError:
            messagebox.showwarning(APP_TITLE, "Use valid 24-hour times such as 06:00 and 18:30.")
            return
        program = self.auto_program_var.get() or self.automation_store.config.default_program
        from scheduler import ScheduleEntry
        self.automation_store.config.schedule.append(ScheduleEntry(day, start, end, program))
        self.automation_store.save()
        self._refresh_automation_ui()

    def _automation_remove_schedule(self) -> None:
        indexes = self.schedule_list.curselection()
        for i in reversed(indexes):
            if i < len(self.automation_store.config.schedule):
                self.automation_store.config.schedule.pop(i)
        self.automation_store.save()
        self._refresh_automation_ui()

    def _refresh_automation_ui(self) -> None:
        if not hasattr(self, "schedule_list"): return
        names = [p.name for p in self.automation_store.config.programs]
        self.auto_program_combo["values"] = names
        if self.auto_program_var.get() not in names:
            self.auto_program_var.set(names[0] if names else "")
        self.schedule_list.delete(0, "end")
        for e in self.automation_store.config.schedule:
            self.schedule_list.insert("end", f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][e.day]}  {e.start}-{e.end}  → {e.program}")

    def _automation_item_started(self, path: str, category: str, program: str) -> None:
        self.after(0, lambda: self._automation_log(f"{category.upper():11} | {program} | {__import__('pathlib').Path(path).name}"))

    def _automation_state(self, text: str) -> None:
        self.after(0, lambda: self.auto_status.config(text=text) if hasattr(self, "auto_status") else None)

    def _automation_next(self) -> None:
        if self._automation_manual_takeover or not self.automation.enabled_now():
            return
        item = self.automation.next_item()
        if not item:
            self._automation_log("No automation content is assigned to the active program.")
            return
        category, path, program = item
        self.automation.on_item(path, category, program)
        if self.player.play_file(path):
            self._automation_owned = True
            self.after(0, lambda: self.auto_status.config(text=f"AUTO • {program} • {category.replace('_',' ').title()}"))
            if category == "commercial":
                self._log_commercial_play(path, program)
        else:
            self.after(0, lambda: self._automation_log(f"Cannot play: {path}"))

    def _tick_automation(self) -> None:
        try:
            if hasattr(self, "auto_clock_var"):
                now = datetime.datetime.now()
                self.auto_clock_var.set(now.strftime("%H:%M:%S"))
                self.auto_clock_name_var.set(self.automation.clock_name(now) + " Clock")
                event = self.automation.next_clock_event(now)
                if event:
                    remaining = self.automation.seconds_to_next_event(now)
                    self.auto_countdown_var.set(f"T-{remaining // 60:02d}:{remaining % 60:02d}")
                    label = event.label or event.category.replace("_", " ").title()
                    self.auto_next_var.set(f"Next clock: {label} @ :{event.minute:02d}")
                self._refresh_automation_preview()
            c = self.automation_store.config
            if c.enabled and not self._automation_manual_takeover:
                self.player.set_auto_advance(False)
                if self.automation.enabled_now() and not self.player.is_playing():
                    self._automation_next()
                elif not self.automation.enabled_now() and self._automation_owned and self.player.is_playing():
                    self._automation_owned = False
                    self.player.stop()
                    self.auto_status.config(text="Outside scheduled hours")
        except Exception as exc:
            if hasattr(self, "auto_status"):
                self.auto_status.config(text=f"Automation error: {exc}")
        self.after(1000, self._tick_automation)

    def _build_nowplaying_tab(self) -> None:
        f = self.tab_nowplaying
        for i in range(2):
            f.columnconfigure(i, weight=1)

        row = 0
        ttk.Label(f, text="Station name").grid(row=row, column=0, sticky="w", padx=10, pady=(14, 4))
        self.station_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.station_var).grid(row=row, column=1, sticky="ew", padx=10, pady=(14, 4))
        row += 1

        ttk.Label(f, text="Genre").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.genre_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.genre_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        ttk.Label(f, text="Description").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.description_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.description_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        ttk.Label(f, text="Website URL").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.website_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.website_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        self.public_var = tk.BooleanVar()
        ttk.Checkbutton(f, text="List publicly on server's directory", variable=self.public_var).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=10, pady=4
        )
        row += 1

        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=10)
        row += 1

        ttk.Label(f, text="Now playing").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.song_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.song_var).grid(row=row, column=1, sticky="ew", padx=10, pady=4)
        row += 1

        ttk.Button(f, text="Push now-playing update", command=self._push_metadata).grid(
            row=row, column=1, sticky="e", padx=10, pady=8
        )

    # ------------------------------------------------------------------
    # Config <-> widgets
    # ------------------------------------------------------------------

    def _load_into_widgets(self) -> None:
        c, a, m = self.config_obj.connection, self.config_obj.audio, self.config_obj.metadata
        proto_display = {"icecast2": "icecast2", "shoutcast2": "shoutcast2", "shoutcast1": "shoutcast1 (legacy)"}
        self.protocol_var.set(proto_display.get(c.protocol, "icecast2"))
        self.host_var.set(c.host)
        self.port_var.set(str(c.port))
        self.mount_var.set(c.mount)
        self.username_var.set(c.username)
        self.password_var.set(c.password)
        self.tls_var.set(c.use_tls)

        self._rebuild_mixer_rows()
        self.codec_var.set(a.codec)
        self.bitrate_var.set(str(a.bitrate_kbps))
        self.samplerate_var.set(str(a.sample_rate))
        self.channels_var.set("1 (mono)" if a.channels == 1 else "2 (stereo)")

        r = self.config_obj.recording
        self.record_dir_var.set(r.directory or str(default_recordings_dir()))
        self.record_format_var.set(r.format)

        self.station_var.set(m.station_name)
        self.genre_var.set(m.genre)
        self.description_var.set(m.description)
        self.website_var.set(m.website_url)
        self.public_var.set(m.public)
        self.song_var.set(m.current_song)

    def _pull_from_widgets(self) -> None:
        c, a, m = self.config_obj.connection, self.config_obj.audio, self.config_obj.metadata
        r = self.config_obj.recording
        proto_map = {"icecast2": "icecast2", "shoutcast2": "shoutcast2", "shoutcast1 (legacy)": "shoutcast1"}
        c.protocol = proto_map.get(self.protocol_var.get(), "icecast2")
        c.host = self.host_var.get().strip()
        try:
            c.port = int(self.port_var.get().strip())
        except ValueError:
            c.port = 8000
        c.mount = self.mount_var.get().strip() or "/stream"
        c.username = self.username_var.get().strip()
        c.password = self.password_var.get()
        c.use_tls = self.tls_var.get()

        self._pull_mixer_from_widgets()
        a.codec = self.codec_var.get() or "mp3"
        try:
            a.bitrate_kbps = int(self.bitrate_var.get())
        except ValueError:
            a.bitrate_kbps = 128
        try:
            a.sample_rate = int(self.samplerate_var.get())
        except ValueError:
            a.sample_rate = 44100
        a.channels = 1 if self.channels_var.get().startswith("1") else 2

        typed_dir = self.record_dir_var.get().strip()
        r.directory = "" if typed_dir == str(default_recordings_dir()) else typed_dir
        r.format = self.record_format_var.get() or "mp3"

        m.station_name = self.station_var.get().strip() or "My Station"
        m.genre = self.genre_var.get().strip()
        m.description = self.description_var.get().strip()
        m.website_url = self.website_var.get().strip()
        m.public = self.public_var.get()
        m.current_song = self.song_var.get().strip()

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def _refresh_devices(self) -> None:
        # Save whatever's currently in the mixer rows before we rebuild them,
        # so editing a fader/label and then refreshing doesn't lose it.
        if self.mixer_rows:
            self._pull_mixer_from_widgets()

        def worker():
            devices = list_audio_devices()
            self.after(0, lambda: self._apply_device_list(devices))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_device_list(self, devices: list[str]) -> None:
        existing = {ch.device: ch for ch in self.config_obj.audio.mixer_channels}
        merged: list[MixerChannel] = []
        for dev in devices:
            if dev in existing:
                merged.append(existing[dev])
                continue
            kind = categorize_device(dev)
            merged.append(
                MixerChannel(
                    device=dev,
                    label=default_label_for(dev),
                    enabled=kind in ("mic", "playback"),
                    volume=1.0,
                )
            )
        self.config_obj.audio.mixer_channels = merged
        self._rebuild_mixer_rows()

    # ------------------------------------------------------------------
    # Recording controls
    # ------------------------------------------------------------------

    def _browse_record_dir(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.record_dir_var.get() or str(default_recordings_dir())
        )
        if chosen:
            self.record_dir_var.set(chosen)

    def _toggle_recording(self) -> None:
        if self.recorder is not None and self.recorder.is_running():
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        self._pull_from_widgets()
        self.config_obj.save()

        self.recorder = Recorder(self.config_obj, on_log=self._queue_log)
        path = self.recorder.start()
        if path is None:
            messagebox.showwarning(APP_TITLE, "Couldn't start recording — check the connection log.")
            self.recorder = None
            return

        self.record_btn.config(text="Stop Recording")
        self.record_dot.itemconfig(self._record_dot_id, fill=RED)
        self.record_status_label.config(text="Recording…")
        self.record_file_label.config(text=str(path))

    def _stop_recording(self) -> None:
        if self.recorder is not None:
            self.recorder.stop()
        self.record_btn.config(text="Start Recording")
        self.record_dot.itemconfig(self._record_dot_id, fill=TEXT_FAINT)
        self.record_status_label.config(text="Not recording")

    def _tick_recording_timer(self) -> None:
        if self.recorder is not None and self.recorder.is_running() and self.recorder.started_at:
            elapsed = datetime.datetime.now() - self.recorder.started_at
            total_seconds = int(elapsed.total_seconds())
            mm, ss = divmod(total_seconds, 60)
            hh, mm = divmod(mm, 60)
            timecode = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
            self.record_status_label.config(text=f"Recording… {timecode}")
        self.after(500, self._tick_recording_timer)
        self.after(1000, self._tick_dashboard)
        self.after(1000, self._tick_automation)

    # ------------------------------------------------------------------
    # Streaming controls
    # ------------------------------------------------------------------

    def _toggle_stream(self) -> None:
        currently_running = self.streamer._state == StreamState.LIVE or \
            self.streamer._state == StreamState.CONNECTING or \
            self.streamer._state == StreamState.RECONNECTING or \
            self._is_legacy_v1_running
        if currently_running:
            self._stop_stream()
        else:
            self._start_stream()

    # ------------------------------------------------------------------
    # Connection test (pre-flight reachability check, no auth attempted)
    # ------------------------------------------------------------------

    def _test_connection(self) -> None:
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            self.test_conn_label.config(text="Enter a valid port number.", foreground=RED)
            return
        if not host:
            self.test_conn_label.config(text="Enter a host first.", foreground=RED)
            return

        self.test_conn_btn.config(state="disabled")
        self.test_conn_label.config(text=f"Testing {host}:{port}…", foreground=TEXT_FAINT)

        def worker():
            try:
                with socket.create_connection((host, port), timeout=6):
                    ok, msg = True, f"Reachable — {host}:{port} accepted a connection."
            except OSError as exc:
                ok, msg = False, f"Not reachable — {exc}"

            def apply():
                self.test_conn_btn.config(state="normal")
                self.test_conn_label.config(text=msg, foreground=GREEN if ok else RED)
            self.after(0, apply)
            self._queue_log(f"[test] {msg}")

        threading.Thread(target=worker, daemon=True).start()

    def _start_stream(self) -> None:
        self._pull_from_widgets()
        self.config_obj.save()

        if self.config_obj.connection.protocol == "shoutcast1":
            self.v1_streamer = ShoutcastV1Streamer(
                self.config_obj, on_log=self._queue_log, on_stats=self._on_stats
            )
            self._is_legacy_v1_running = True
            self.connect_btn.config(text="Stop")
            self._on_state_change(StreamState.CONNECTING, "Connecting to server…")

            def worker():
                ok = self.v1_streamer.start()
                if ok:
                    self.after(0, lambda: self._on_state_change(StreamState.LIVE, "Live (legacy v1)"))
                else:
                    self.after(0, lambda: self._on_state_change(StreamState.ERROR, "Legacy connect failed"))
                    self._is_legacy_v1_running = False

            threading.Thread(target=worker, daemon=True).start()
        else:
            self.streamer.start()
            self.connect_btn.config(text="Stop")

    def _stop_stream(self) -> None:
        if self._is_legacy_v1_running and self.v1_streamer:
            self.v1_streamer.stop()
            self._is_legacy_v1_running = False
        self._stream_started_at = None
        self._last_bitrate = 0.0
        self._last_elapsed = 0.0
        self.streamer.stop()
        self.connect_btn.config(text="Go Live")

    def _push_metadata(self) -> None:
        self._pull_from_widgets()
        self.config_obj.save()
        song = self.song_var.get().strip()
        if not song:
            messagebox.showinfo(APP_TITLE, "Enter a now-playing title first.")
            return

        def worker():
            ok = self.streamer.push_metadata(song)
            if not ok:
                self.after(0, lambda: messagebox.showwarning(
                    APP_TITLE, "Metadata push failed — check the connection log."
                ))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # State / logging
    # ------------------------------------------------------------------

    def _on_state_change(self, state: StreamState, message: str) -> None:
        def apply():
            color = STATE_COLORS.get(state, TEXT_FAINT)
            self.status_dot.itemconfig(self._dot_id, fill=color)
            self.status_label.config(text=message, fg=TEXT if state != StreamState.ERROR else RED)
            if hasattr(self, "dashboard_state"):
                live = state == StreamState.LIVE
                self.dashboard_state.config(text="ON AIR" if live else state.value.upper().replace("_", " "), fg=GREEN if live else STATE_COLORS.get(state, TEXT_FAINT))
                self.dashboard_live_dot.itemconfig(self._dashboard_dot, fill=STATE_COLORS.get(state, TEXT_FAINT))
            if state == StreamState.LIVE and self._stream_started_at is None:
                self._stream_started_at = datetime.datetime.now()
            elif state in (StreamState.STOPPED, StreamState.ERROR):
                self._stream_started_at = None
            if state in (StreamState.STOPPED, StreamState.ERROR):
                self.connect_btn.config(text="Go Live")
                self.stats_label.config(text="")
        self.after(0, apply)
        self._queue_log(f"[state] {state.value}: {message}")

    def _on_stats(self, elapsed_seconds: float, bitrate_kbps: float) -> None:
        """Fires on every ffmpeg progress line from whichever streamer is
        active. Turns raw log noise into a single readable readout instead
        of making the user parse scrolling ffmpeg output to check health."""
        total = int(elapsed_seconds)
        hh, rem = divmod(total, 3600)
        mm, ss = divmod(rem, 60)
        timecode = f"{hh:02d}:{mm:02d}:{ss:02d}" if hh else f"{mm:02d}:{ss:02d}"
        self._last_bitrate = bitrate_kbps
        self._last_elapsed = elapsed_seconds
        text = f"· {bitrate_kbps:.0f} kbps · {timecode}"
        self.after(0, lambda: self.stats_label.config(text=text))

    def _tick_dashboard(self) -> None:
        if hasattr(self, "dashboard_cards"):
            c = self.config_obj.connection
            endpoint = f"{c.host}:{c.port}" if c.host else "Not configured"
            self.dashboard_cards[0].config(text=endpoint)
            self.dashboard_cards[1].config(text=f"{self._last_bitrate:.0f} kbps" if self._last_bitrate else "—")
            self.dashboard_cards[2].config(text=self.config_obj.audio.codec.upper())
            recording = self.recorder is not None and self.recorder.is_running()
            self.dashboard_cards[3].config(text="ON" if recording else "OFF", fg=RED if recording else TEXT)
            if self._stream_started_at:
                elapsed = int((datetime.datetime.now() - self._stream_started_at).total_seconds())
                hh, rem = divmod(elapsed, 3600)
                mm, ss = divmod(rem, 60)
                self.dashboard_clock.config(text=f"{hh:02d}:{mm:02d}:{ss:02d}")
            else:
                self.dashboard_clock.config(text="00:00:00")
        self.after(1000, self._tick_dashboard)
        self.after(1000, self._tick_automation)

    def _queue_log(self, line: str) -> None:
        self._log_queue.put(line)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self.log_widget.config(state="normal")
                self.log_widget.insert("end", line + "\n")
                self.log_widget.see("end")
                self.log_widget.config(state="disabled")
        except queue.Empty:
            pass
        self.after(150, self._drain_log_queue)

    def _on_close(self) -> None:
        self._pull_from_widgets()
        self.config_obj.save()
        self.streamer.stop()
        if self.v1_streamer:
            self.v1_streamer.stop()
        if self.recorder is not None:
            self.recorder.stop()
        self.player.stop()
        self.destroy()
