"""
mixer.py — shared logic for combining multiple captured audio sources
(microphone, playback/loopback device, or any other dshow-visible device)
into a single mixed stream, so every source gets its own independent
volume fader before either the Icecast/Shoutcast-v2 streamer or the
legacy Shoutcast-v1 streamer ever sees the audio.

Both streamer.py and shoutcast_v1.py call build_mixer_ffmpeg_args() so the
mixing behaviour (and any future tweaks to it) stays identical everywhere
audio is captured and encoded.
"""
from __future__ import annotations

from typing import List, Tuple

from config import MixerChannel


def active_channels(channels: List[MixerChannel]) -> List[MixerChannel]:
    """Channels that should actually be captured right now: enabled and
    not muted. Disabled/muted channels are left out of the ffmpeg command
    entirely, so they cost nothing while off."""
    return [c for c in channels if c.enabled and not c.muted]


def build_mixer_ffmpeg_args(channels: List[MixerChannel]) -> Tuple[List[str], List[str]]:
    """
    Build the ffmpeg input arguments and the -filter_complex/-map arguments
    needed to capture every active mixer channel (mic, playback device, or
    any other connected device) and combine them into one output stream,
    applying each channel's own fader (volume) before mixing.

    Returns (input_args, mix_args):
      - input_args: one "-f dshow -i audio=<device>" pair per active channel,
        in a fixed order that lines up with the [N:a] labels used below.
      - mix_args: "-filter_complex ... -map [...]" wired to that mixed output
        (empty list if there's nothing to mix — see the fallback below).

    With zero active channels, returns empty input/mix arguments. The caller
    should refuse to start because DirectShow does not provide a portable
    ``audio=default`` device name on Windows.
    """
    active = active_channels(channels)

    if not active:
        return ([], [])

    input_args: List[str] = []
    for ch in active:
        input_args += ["-f", "dshow", "-i", f"audio={ch.device}"]

    # Each source gets its own volume filter first — this *is* the fader —
    # before anything is summed together.
    filter_parts = []
    for idx, ch in enumerate(active):
        gain = max(0.0, ch.volume)
        filter_parts.append(f"[{idx}:a]volume={gain:.3f}[a{idx}]")

    if len(active) == 1:
        # Single source: still passed through its own fader, just no amix
        # step needed since there's nothing else to blend it with.
        out_label = "a0"
    else:
        joined = "".join(f"[a{i}]" for i in range(len(active)))
        filter_parts.append(
            f"{joined}amix=inputs={len(active)}:duration=longest:dropout_transition=3[aout]"
        )
        out_label = "aout"

    mix_args = ["-filter_complex", ";".join(filter_parts), "-map", f"[{out_label}]"]
    return (input_args, mix_args)
