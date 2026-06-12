"""Session persistence: every editor run is recorded and can be resumed or forked.

The conversation itself is persisted as JSONL by the Claude CLI (keyed by session id
under a cwd-derived project dir — runner pins cwd to the workspace home so ids stay
resolvable no matter where cutroom was invoked). This module keeps the cutroom side:

- sessions/index.jsonl  — one record per run (task, spend, turns, lineage)
- sessions/<id>.json    — evidence state (viewed frames) carried across resume/fork,
  so the evidence gate keeps honoring receipts the agent actually earned earlier
  instead of forcing it to re-view every frame.

Fork is the editing killer feature: branch one explored session into competing
cut styles and compare, without re-paying the investigation budget.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from cutroom.db import Workspace


def record_session(
    ws: Workspace,
    video_id: str,
    *,
    session_id: str,
    task: str,
    turns: int,
    spent: int,
    ok: bool,
    edl: bool,
    resumed_from: str = "",
    forked: bool = False,
    role: str = "editor",
) -> None:
    record = {
        "session_id": session_id,
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": " ".join(task.split())[:200],
        "turns": turns,
        "spent": spent,
        "ok": ok,
        "edl": edl,
        "resumed_from": resumed_from,
        "forked": forked,
        "role": role,
    }
    index = ws.sessions_dir(video_id) / "index.jsonl"
    with index.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def list_sessions(ws: Workspace, video_id: str) -> list[dict[str, Any]]:
    """Latest record per session id, in first-seen order (a resumed session keeps its
    id, so its row updates in place rather than duplicating)."""
    index = ws.sessions_dir(video_id) / "index.jsonl"
    if not index.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    for line in index.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("session_id"):
            latest[rec["session_id"]] = rec
    return list(latest.values())


def resolve_session(ws: Workspace, video_id: str, ref: str) -> str:
    """Expand a session-id prefix to the full id; raise with a hint when it doesn't
    name exactly one known session (friendly CLI boundary turns that into one line)."""
    matches = [s["session_id"] for s in list_sessions(ws, video_id)
               if s["session_id"].startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    problem = "is ambiguous" if matches else "matches no session"
    raise ValueError(f"session {ref!r} {problem} — try `cutroom sessions {video_id}`")


def save_state(ws: Workspace, video_id: str, session_id: str, viewed_frames: list[float]) -> None:
    path = ws.sessions_dir(video_id) / f"{session_id}.json"
    path.write_text(json.dumps({"viewed_frames": viewed_frames}), encoding="utf-8")


def load_state(ws: Workspace, video_id: str, session_id: str) -> list[float]:
    path = ws.sessions_dir(video_id) / f"{session_id}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [float(t) for t in data.get("viewed_frames", [])]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
