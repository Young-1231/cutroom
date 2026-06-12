"""Shadow-VCS over the EDL: "undo to before that cut", independent of any agent session.

Every accepted or saved edit list becomes an immutable, content-deduped checkpoint
under media/<video>/checkpoints/. Not git: the state is one small JSON document, so
snapshots are plain JSON files and diffs are computed cut-aware (moved/trimmed/added/
removed cuts) instead of line-based. Restore never destroys state — the current
edl.json is checkpointed first, so a restore is itself undoable.

Granularity note: Cline-style "restore task as well" needs persistent sessions;
until JSONL resume/fork lands, restore means the EDL file only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cutroom.db import Workspace


@dataclass
class CheckpointMeta:
    id: str
    ts: str
    source: str  # agent | plan | render | pre-restore
    session: str
    n_cuts: int
    total_secs: float


def _files(ws: Workspace, video_id: str) -> list[Path]:
    return sorted(ws.checkpoints_dir(video_id).glob("cp_*.json"))


def _canon(edl_dict: dict[str, Any]) -> str:
    return json.dumps(edl_dict, sort_keys=True)


def save_checkpoint(
    ws: Workspace, video_id: str, edl_dict: dict[str, Any], source: str, session: str = ""
) -> str | None:
    """Snapshot one EDL state; returns the checkpoint id, or None when the EDL is
    byte-identical to the latest checkpoint (saving again would just be noise)."""
    existing = _files(ws, video_id)
    if existing:
        latest = json.loads(existing[-1].read_text(encoding="utf-8"))
        if _canon(latest["edl"]) == _canon(edl_dict):
            return None
        seq = int(existing[-1].stem.split("_")[1]) + 1
    else:
        seq = 1
    cp_id = f"cp_{seq:04d}"
    record = {
        "id": cp_id,
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": source,
        "session": session,
        "edl": edl_dict,
    }
    path = ws.checkpoints_dir(video_id) / f"{cp_id}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return cp_id


def load_checkpoint(ws: Workspace, video_id: str, cp_id: str) -> dict[str, Any]:
    path = ws.checkpoints_dir(video_id) / f"{cp_id}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no checkpoint {cp_id!r} for {video_id} — try `cutroom checkpoints {video_id}`"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def list_checkpoints(ws: Workspace, video_id: str) -> list[CheckpointMeta]:
    out: list[CheckpointMeta] = []
    for path in _files(ws, video_id):
        rec = json.loads(path.read_text(encoding="utf-8"))
        cuts = rec["edl"].get("cuts") or []
        out.append(CheckpointMeta(
            id=rec["id"], ts=rec["ts"], source=rec.get("source", ""),
            session=rec.get("session", ""), n_cuts=len(cuts),
            total_secs=sum(float(c["t1"]) - float(c["t0"]) for c in cuts),
        ))
    return out


def restore_checkpoint(
    ws: Workspace, video_id: str, cp_id: str
) -> tuple[str | None, Path]:
    """Write a checkpoint's EDL back to renders/edl.json.

    The current edl.json (if any) is checkpointed as source="pre-restore" first;
    a corrupt current file is moved aside instead of silently overwritten.
    Returns (pre_restore_checkpoint_id, edl_path).
    """
    rec = load_checkpoint(ws, video_id, cp_id)
    edl_path = ws.renders_dir(video_id) / "edl.json"
    pre_id: str | None = None
    if edl_path.exists():
        try:
            current = json.loads(edl_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            edl_path.replace(edl_path.with_suffix(".json.corrupt"))
        else:
            pre_id = save_checkpoint(ws, video_id, current, "pre-restore")
    edl_path.write_text(
        json.dumps(rec["edl"], indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return pre_id, edl_path


def diff_edls(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Cut-aware diff, one human-readable line per change; empty means identical."""

    def span(c: dict[str, Any]) -> str:
        return f"[{float(c['t0']):.2f}-{float(c['t1']):.2f}]"

    lines: list[str] = []
    oc, nc = old.get("cuts") or [], new.get("cuts") or []
    for i in range(max(len(oc), len(nc))):
        if i >= len(oc):
            label = nc[i].get("label", "")
            lines.append(f"+ cut {i} {span(nc[i])}" + (f" {label}" if label else ""))
        elif i >= len(nc):
            label = oc[i].get("label", "")
            lines.append(f"- cut {i} {span(oc[i])}" + (f" {label}" if label else ""))
        else:
            a, b = oc[i], nc[i]
            if (float(a["t0"]), float(a["t1"])) != (float(b["t0"]), float(b["t1"])):
                lines.append(f"~ cut {i} {span(a)} -> {span(b)}")
            if a.get("label", "") != b.get("label", ""):
                lines.append(
                    f"~ cut {i} label {a.get('label', '')!r} -> {b.get('label', '')!r}"
                )
    for key in ("target", "captions"):
        if old.get(key) != new.get(key):
            lines.append(f"~ {key} {old.get(key)} -> {new.get(key)}")
    return lines
