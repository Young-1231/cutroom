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
    """Word-timestamped transcript intersecting [t0, t1], truncated to max_chars."""
    if t1 <= t0:
        return f"empty span ({t0:.1f}–{t1:.1f})"
    segments = ws.get_segments(video_id, t0, t1)
    if not segments:
        return f"no transcript between {fmt_ts(t0)} and {fmt_ts(t1)}"

    lines: list[str] = []
    used = 0
    truncated = False
    for seg in segments:
        line = f"(seg {seg.id}) [{seg.t0:.1f}–{seg.t1:.1f}] {seg.text.strip()}"
        if used + len(line) > max_chars:
            # Keep whole words up to the cap.
            room = max(0, max_chars - used - 1)
            cut = line[:room]
            if " " in cut:
                cut = cut[: cut.rfind(" ")]
            if cut:
                lines.append(cut + "…")
            truncated = True
            break
        lines.append(line)
        used += len(line) + 1

    if not truncated:
        return "\n".join(lines)
    # The truncation marker must fit inside the cap too — drop content lines until it does.
    while True:
        last_t = segments[len(lines) - 1].t1 if lines else t0
        marker = f"[truncated at {fmt_ts(last_t)} — call read_transcript again from there]"
        out = "\n".join([*lines, marker])
        if len(out) <= max_chars or not lines:
            return out
        lines.pop()
