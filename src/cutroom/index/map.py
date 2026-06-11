"""Build scenes and render the compact hierarchical video map.

The map is the core artifact of cutroom: a few-KB overview the agent reads instead
of the full transcript, regardless of how long the source video is.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from cutroom.db import Workspace
from cutroom.types import AudioEvent, Scene, Segment, Shot

# A silence this long is treated as a scene boundary.
SCENE_SILENCE_SECONDS = 2.0
# Snap a silence-derived boundary to a shot boundary only when it is this close.
SNAP_WINDOW_SECONDS = 1.5


def fmt_ts(seconds: float) -> str:
    """mm:ss, or h:mm:ss past the hour."""
    s = max(0, int(round(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def build_scenes(
    shots: list[Shot],
    segments: list[Segment],
    audio_events: list[AudioEvent],
    video_id: str,
    duration: float,
    max_scene_seconds: float = 180.0,
    min_scene_seconds: float = 8.0,
    target_scene_seconds: float = 90.0,
) -> list[Scene]:
    """Pure scene segmentation.

    Long silences are always boundaries (snapped to a nearby shot boundary when one
    is close). When speech is continuous — short pauses only, common in narrated or
    lecture footage — that rule alone degenerates into max_scene_seconds equal
    splits, so we top up with the *strongest* weak pauses (short silences and
    inter-segment gaps) until scenes average ~target_scene_seconds. The equal-split
    cap stays as a last resort.
    """
    shot_bounds = sorted({s.t0 for s in shots} | {s.t1 for s in shots})

    def snapped(t: float) -> float:
        near = min(shot_bounds, key=lambda b: abs(b - t), default=t)
        return near if abs(near - t) <= SNAP_WINDOW_SECONDS else t

    # Strong silences split greedily by length with a minimum separation — dialogue-dense
    # footage has clusters of 2s beats that would otherwise produce fragment scenes.
    bounds: set[float] = set()
    for _, t in sorted(
        ((ev.t1 - ev.t0, snapped((ev.t0 + ev.t1) / 2))
         for ev in audio_events
         if ev.kind == "silence" and (ev.t1 - ev.t0) >= SCENE_SILENCE_SECONDS),
        reverse=True,
    ):
        if all(abs(t - b) >= min_scene_seconds for b in bounds):
            bounds.add(t)

    # Weak-pause candidates, strongest first: short silences + gaps between segments.
    candidates: list[tuple[float, float]] = []  # (strength, time)
    for ev in audio_events:
        if ev.kind == "silence" and 0.4 <= (ev.t1 - ev.t0) < SCENE_SILENCE_SECONDS:
            candidates.append((ev.t1 - ev.t0, (ev.t0 + ev.t1) / 2))
    for a, b in zip(segments, segments[1:], strict=False):
        gap = b.t0 - a.t1
        if gap >= 0.4:
            candidates.append((gap, a.t1 + gap / 2))
    candidates.sort(reverse=True)

    # Long footage gets proportionally longer scenes so the map stays a few KB:
    # ~20 scenes max regardless of duration (a 90-min film ≈ 4-5 min scenes).
    effective_target_seconds = max(target_scene_seconds, duration / 20)
    target = max(1, round(duration / effective_target_seconds))
    for _, t in candidates:
        if len(bounds) + 1 >= target:
            break
        t = snapped(t)
        if all(abs(t - b) >= min_scene_seconds for b in bounds):
            bounds.add(t)

    edges = sorted(b for b in bounds if min_scene_seconds < b < duration - min_scene_seconds)
    starts = [0.0, *edges]
    ends = [*edges, duration]

    scenes: list[Scene] = []
    for t0, t1 in zip(starts, ends, strict=True):
        # Split oversized spans into equal chunks under the cap.
        n = max(1, math.ceil((t1 - t0) / max_scene_seconds))
        step = (t1 - t0) / n
        for i in range(n):
            a, b = t0 + i * step, t0 + (i + 1) * step
            scenes.append(
                Scene(id=None, video_id=video_id, t0=a, t1=b,
                      title=_title_for(a, b, segments, len(scenes) + 1),
                      summary=_summary_for(a, b, segments))
            )
    return scenes


def _segments_in(t0: float, t1: float, segments: list[Segment]) -> list[Segment]:
    return [s for s in segments if s.t1 > t0 and s.t0 < t1]


def _title_for(t0: float, t1: float, segments: list[Segment], n: int) -> str:
    inside = _segments_in(t0, t1, segments)
    if not inside:
        return f"scene {n}"
    words = inside[0].text.split()
    return " ".join(words[:6]) + ("…" if len(words) > 6 else "")


def _summary_for(t0: float, t1: float, segments: list[Segment]) -> str:
    inside = _segments_in(t0, t1, segments)
    if not inside:
        return "(no speech)"
    text = inside[0].text.strip()
    return text[:100] + ("…" if len(text) > 100 else "")


def build_and_store_scenes(
    ws: Workspace, video_id: str, summarizer: Callable[[str], str] | None = None
) -> list[Scene]:
    meta = ws.get_video(video_id)
    if meta is None:
        raise ValueError(f"unknown video {video_id!r}")
    scenes = build_scenes(
        ws.get_shots(video_id),
        ws.get_segments(video_id),
        ws.get_audio_events(video_id),
        video_id,
        meta.duration,
    )
    if summarizer is not None:
        for sc in scenes:
            text = " ".join(s.text for s in _segments_in(sc.t0, sc.t1, ws.get_segments(video_id)))
            if text.strip():
                try:
                    sc.summary = str(summarizer(text)).strip()[:140]
                except Exception:
                    pass  # heuristic summary already in place; LLM polish is best-effort
    ws.replace_scenes(video_id, scenes)
    return ws.get_scenes(video_id)


def render_video_map(ws: Workspace, video_id: str) -> str:
    """The compact overview the agent starts from. Stays a few KB even for hours-long video."""
    meta = ws.get_video(video_id)
    if meta is None:
        return f"unknown video {video_id!r}"
    shots = ws.get_shots(video_id)
    segments = ws.get_segments(video_id)
    scenes = ws.get_scenes(video_id)
    events = ws.get_audio_events(video_id)

    lines = [
        f"🎬 {meta.title or meta.id} — {fmt_ts(meta.duration)}"
        f" · {len(shots)} shots · {len(segments)} segments · {len(scenes)} scenes"
    ]
    # Keep the whole map compact: shrink per-scene summaries as the scene count grows.
    sum_cap = 90 if len(scenes) <= 30 else 45
    for i, sc in enumerate(scenes, 1):
        n_shots = sum(1 for s in shots if s.t1 > sc.t0 and s.t0 < sc.t1)
        inside = _segments_in(sc.t0, sc.t1, segments)
        speech = sum(min(s.t1, sc.t1) - max(s.t0, sc.t0) for s in inside)
        pct = int(100 * speech / (sc.t1 - sc.t0)) if sc.t1 > sc.t0 else 0
        summary = sc.summary[:sum_cap] + ("…" if len(sc.summary) > sum_cap else "")
        lines.append(
            f"S{i} [{fmt_ts(sc.t0)}–{fmt_ts(sc.t1)}] {sc.title} — {summary}"
            f" ({n_shots} shots, speech {pct}%)"
        )
    silences = [e for e in events if e.kind == "silence"][:20]
    if silences:
        lines.append("silences: " + ", ".join(f"{fmt_ts(e.t0)}–{fmt_ts(e.t1)}" for e in silences))
    louds = [e for e in events if e.kind == "loud"][:10]
    if louds:
        lines.append("loud moments: " + ", ".join(fmt_ts(e.t0) for e in louds))
    return "\n".join(lines)
