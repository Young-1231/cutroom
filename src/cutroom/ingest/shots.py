"""Shot detection via ffmpeg scene-change scores.

Static footage (talking heads, screencasts) may yield no boundaries; we fall
back to fixed windows so the video map always has shot-level granularity.
"""

from __future__ import annotations

import re
import subprocess

from cutroom.db import Workspace
from cutroom.types import Shot

MIN_SHOT_SECONDS = 0.5
WINDOW_SECONDS = 60.0

_PTS_TIME = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def detect_shots(ws: Workspace, video_id: str, threshold: float = 0.27) -> list[Shot]:
    """Detect scene changes, build contiguous shots covering [0, duration], store them."""
    meta = ws.get_video(video_id)
    if meta is None:
        raise ValueError(f"unknown video: {video_id}")
    duration = meta.duration

    proc = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-nostats",
            "-i", str(ws.source_path(video_id)), "-an",
            # rgb24 first: the scene SAD is luma-weighted and misses chroma-only
            # cuts in YUV (e.g. red -> green keeps Y nearly constant).
            "-vf", f"format=rgb24,select='gt(scene,{threshold})',showinfo",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    cuts = sorted({float(m) for m in _PTS_TIME.findall(proc.stderr) if 0 < float(m) < duration})

    if len(cuts) < 2:
        bounds = _fixed_windows(duration)
    else:
        bounds = [0.0]
        for t in cuts:
            # Dropping a boundary merges the would-be short shot into its left neighbor.
            if t - bounds[-1] >= MIN_SHOT_SECONDS and duration - t >= MIN_SHOT_SECONDS:
                bounds.append(t)
        bounds.append(duration)

    shots = [Shot(id=None, video_id=video_id, t0=t0, t1=t1)
             for t0, t1 in zip(bounds, bounds[1:], strict=False)]
    ws.add_shots(shots)
    return shots


def _fixed_windows(duration: float) -> list[float]:
    if duration < 90:
        return [0.0, duration]
    bounds = [float(t) for t in range(0, int(duration), int(WINDOW_SECONDS))]
    if duration - bounds[-1] < MIN_SHOT_SECONDS:
        bounds.pop()
    return [*bounds, duration]
