"""Budgeted transcript access: sanitized FTS search and capped span reads.

search_transcript returns Segment objects (the agent toolkit renders them);
read_span returns pre-rendered text so the cap is enforced here, where the data is.
"""

from __future__ import annotations

import re
import sqlite3

from cutroom.db import Workspace
from cutroom.index.map import fmt_ts
from cutroom.types import Segment


def search_transcript(
    ws: Workspace, video_id: str, query: str, limit: int = 8
) -> list[Segment] | str:
    """FTS5 search that can never crash on user text: tokens are quoted and OR-joined."""
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        return f"empty or unsearchable query {query!r}"
    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    try:
        return ws.fts_search(video_id, fts_query, limit=limit)
    except sqlite3.OperationalError as e:
        return f"search failed for {query!r}: {e}"


def read_span(ws: Workspace, video_id: str, t0: float, t1: float, max_chars: int = 2000) -> str:
    """Word-timestamped transcript intersecting [t0, t1], emitting only whole segments
    that fit within max_chars.

    The resume point is the start of the first segment that did NOT fully fit, so the
    next call re-includes it in full (get_segments filters with `t1 > t0`, so resuming
    at a segment's t1 would silently drop it). When not even the first segment fits, we
    say so instead of advertising a resume point that would loop forever."""
    if t1 <= t0:
        return f"empty span ({t0:.1f}–{t1:.1f})"
    segments = ws.get_segments(video_id, t0, t1)
    if not segments:
        return f"no transcript between {fmt_ts(t0)} and {fmt_ts(t1)}"

    lines: list[str] = []
    used = 0
    resume_at: float | None = None
    for seg in segments:
        line = f"(seg {seg.id}) [{seg.t0:.1f}–{seg.t1:.1f}] {seg.text.strip()}"
        # Reserve room for the resume marker so it never overflows the cap.
        marker = f"[truncated — read again from {fmt_ts(seg.t0)}]"
        if used + len(line) > max_chars - len(marker) - 1:
            resume_at = seg.t0
            break
        lines.append(line)
        used += len(line) + 1

    if resume_at is None:
        return "\n".join(lines)
    if not lines:
        need = used + len(segments[0].text)
        return (
            f"budget too small to read even the first segment here"
            f" (need ~{need} chars, have {max_chars}); free up budget or read a narrower span"
        )
    return "\n".join([*lines, f"[truncated — read again from {fmt_ts(resume_at)}]"])
