"""Non-speech audio signals: silences (ffmpeg silencedetect) and loud spans (ebur128).

Silences are reliable and feed scene building; loud spans are best-effort
salience hints — an empty list is fine if the loudness log can't be parsed.
"""

from __future__ import annotations

import re
import statistics
import subprocess

from cutroom.db import Workspace
from cutroom.types import AudioEvent

SILENCE_NOISE_DB = -35
SILENCE_MIN_SECONDS = 1.0
LOUD_OVER_MEDIAN_DB = 8.0
# ebur128 logs momentary loudness every ~100ms; larger gaps split loud spans.
_LOUD_GAP_SECONDS = 0.5
_LOUD_MIN_SAMPLES = 3

_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
_EBUR_LINE = re.compile(r"t:\s*([0-9.]+)\s.*?M:\s*(-?[0-9.]+)")


def detect_audio_events(ws: Workspace, video_id: str) -> list[AudioEvent]:
    """Detect silence and loud spans on audio.wav, store and return them."""
    meta = ws.get_video(video_id)
    duration = meta.duration if meta else 0.0
    wav = str(ws.audio_path(video_id))

    events = _silences(wav, video_id, duration)
    try:
        events += _loud_spans(wav, video_id)
    except Exception:  # noqa: BLE001 — best-effort by design, see module docstring
        pass
    ws.add_audio_events(events)
    return events


def _silences(wav: str, video_id: str, duration: float) -> list[AudioEvent]:
    stderr = _ffmpeg_filter_log(
        wav, f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}"
    )
    events: list[AudioEvent] = []
    start: float | None = None
    for line in stderr.splitlines():
        if m := _SILENCE_START.search(line):
            start = float(m.group(1))
        elif (m := _SILENCE_END.search(line)) and start is not None:
            events.append(AudioEvent(None, video_id, "silence", start, float(m.group(1)), -60.0))
            start = None
    if start is not None and duration > start:  # silence ran to EOF
        events.append(AudioEvent(None, video_id, "silence", start, duration, -60.0))
    return events


def _loud_spans(wav: str, video_id: str) -> list[AudioEvent]:
    stderr = _ffmpeg_filter_log(wav, "ebur128")
    samples = [(float(t), float(m)) for t, m in _EBUR_LINE.findall(stderr)]
    if not samples:
        return []
    threshold = statistics.median(m for _, m in samples) + LOUD_OVER_MEDIAN_DB

    events: list[AudioEvent] = []
    span: list[tuple[float, float]] = []
    for t, m in samples:
        if m > threshold and (not span or t - span[-1][0] <= _LOUD_GAP_SECONDS):
            span.append((t, m))
            continue
        if len(span) >= _LOUD_MIN_SAMPLES:
            events.append(_loud_event(video_id, span))
        span = [(t, m)] if m > threshold else []
    if len(span) >= _LOUD_MIN_SAMPLES:
        events.append(_loud_event(video_id, span))
    return events


def _loud_event(video_id: str, span: list[tuple[float, float]]) -> AudioEvent:
    return AudioEvent(None, video_id, "loud", span[0][0], span[-1][0],
                      value=max(m for _, m in span))


def _ffmpeg_filter_log(wav: str, af: str) -> str:
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", wav,
         "-af", af, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return proc.stderr
