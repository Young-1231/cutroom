"""Read and aggregate the per-video audit trail (renders/trail.jsonl).

The hooks layer appends one JSON line per tool call, denial, tool error, and session
stop (see cutroom.agent.hooks). Several sessions may interleave in one file — fan-out
scouts share it — so aggregation groups by the session id every record carries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionTrail:
    session: str
    started: str = ""
    ended: str = ""
    calls: int = 0
    denies: int = 0
    errors: int = 0
    spent: int = 0
    breakdown: dict = field(default_factory=dict)
    moments: int = 0
    edl: bool = False


def read_trail(path: Path) -> list[dict]:
    """All parseable records, oldest first. Corrupt lines are skipped, not fatal —
    the trail is an audit aid; one bad line must not hide the rest."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def group_sessions(records: list[dict]) -> list[SessionTrail]:
    """Per-session aggregates, in order of first appearance."""
    sessions: dict[str, SessionTrail] = {}
    for rec in records:
        sid = str(rec.get("session", ""))
        st = sessions.setdefault(sid, SessionTrail(session=sid))
        ts = str(rec.get("ts", ""))
        st.started = st.started or ts
        st.ended = ts or st.ended
        event = rec.get("event")
        if event == "tool":
            st.calls += 1
            st.spent = max(st.spent, int(rec.get("spent", 0) or 0))
        elif event == "deny":
            st.denies += 1
        elif event == "tool_error":
            st.errors += 1
        elif event == "stop":
            st.spent = int(rec.get("spent", st.spent) or 0)
            st.breakdown = rec.get("breakdown") or {}
            st.moments = int(rec.get("moments", 0) or 0)
            st.edl = bool(rec.get("edl"))
    return list(sessions.values())


def session_records(records: list[dict], session_prefix: str) -> list[dict]:
    """Records of the one session matching the prefix (errors on ambiguity)."""
    matches = {
        str(r.get("session", "")) for r in records
        if str(r.get("session", "")).startswith(session_prefix)
    }
    if not matches:
        raise ValueError(f"no trail records for session {session_prefix!r}")
    if len(matches) > 1:
        short = ", ".join(sorted(m[:8] for m in matches))
        raise ValueError(f"session prefix {session_prefix!r} is ambiguous: {short}")
    sid = matches.pop()
    return [r for r in records if str(r.get("session", "")) == sid]
