"""Repurpose-task scorecard: mechanical metrics over real editor runs.

The AgenticVBench Repurpose family measures whether an agent can turn long footage
into a deliverable under constraints (duration, format) without losing itself in the
context. This module scores the parts that are mechanically checkable, with no LLM
judge: did an EDL land, does it hit the duration/cut-count constraints, does every
cut carry receipts, and how close do boundaries sit to natural speech/silence edges.

Quality judgment ("is the teaser good?") is intentionally out of scope here — that
needs the benchmark's own judging protocol. These metrics are the honest, falsifiable
floor: an agent that fails them has lost the plot regardless of taste.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import median

from cutroom.db import Workspace

# A boundary is "clean" when it sits within this many seconds of a transcript segment
# edge or a detected silence edge — the natural cut points the editor is told to use.
CLEAN_BOUNDARY_SECS = 0.5


def natural_edges(ws: Workspace, video_id: str) -> list[float]:
    edges: set[float] = set()
    for s in ws.get_segments(video_id):
        edges.add(s.t0)
        edges.add(s.t1)
    for e in ws.get_audio_events(video_id, kind="silence"):
        edges.add(e.t0)
        edges.add(e.t1)
    return sorted(edges)


def boundary_distances(edges: list[float], edl_dict: dict) -> list[float]:
    """Distance from every cut boundary to the nearest natural edge."""
    if not edges:
        return []
    out = []
    for c in edl_dict.get("cuts", []):
        for b in (c["t0"], c["t1"]):
            out.append(min(abs(b - e) for e in edges))
    return out


def score_edl(ws: Workspace, video_id: str, edl_dict: dict | None, spec: dict) -> dict:
    """Mechanical scorecard for one task. spec keys (all optional):
    target_secs + tolerance (fraction), n_cuts, min_cut/max_cut per-cut bounds."""
    if not edl_dict or not edl_dict.get("cuts"):
        return {"ok": False, "produced": False}
    cuts = edl_dict["cuts"]
    durations = [c["t1"] - c["t0"] for c in cuts]
    total = sum(durations)
    checks: dict[str, bool] = {}
    if spec.get("target_secs"):
        tol = spec.get("tolerance", 0.25)
        checks["duration"] = (
            abs(total - spec["target_secs"]) <= tol * spec["target_secs"]
        )
    if spec.get("n_cuts"):
        checks["n_cuts"] = len(cuts) == spec["n_cuts"]
    if spec.get("min_cut") is not None or spec.get("max_cut") is not None:
        lo = spec.get("min_cut", 0.0)
        hi = spec.get("max_cut", float("inf"))
        checks["cut_lengths"] = all(lo <= d <= hi for d in durations)
    if spec.get("vertical"):
        checks["target"] = edl_dict.get("target") == "vertical"
    checks["receipts"] = all(
        (c.get("evidence") or {}).get("segment_ids")
        and (c.get("evidence") or {}).get("frame_ts")
        for c in cuts
    )
    dists = boundary_distances(natural_edges(ws, video_id), edl_dict)
    checks["boundaries"] = bool(dists) and max(dists) <= CLEAN_BOUNDARY_SECS
    return {
        "ok": all(checks.values()),
        "produced": True,
        "checks": checks,
        "n_cuts": len(cuts),
        "total_secs": round(total, 2),
        "boundary_max": round(max(dists), 3) if dists else None,
        "boundary_p50": round(median(dists), 3) if dists else None,
    }


def load_tasks(path: Path) -> list[dict]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not all(
        isinstance(t, dict) and t.get("name") and t.get("instruction") for t in tasks
    ):
        raise ValueError(f"{path}: expected a list of {{name, instruction, ...}} tasks")
    return tasks


def markdown_table(rows: list[dict]) -> str:
    head = ("| task | ok | cuts | total | checks | budget | turns |\n"
            "|---|---|---|---|---|---|---|\n")
    lines = []
    for r in rows:
        s = r["score"]
        if not s.get("produced"):
            checks = "no EDL produced"
        else:
            checks = ", ".join(f"{'✓' if v else '✗'} {k}" for k, v in s["checks"].items())
        lines.append(
            f"| {r['task']} | {'✅' if s.get('ok') else '❌'}"
            f" | {s.get('n_cuts', '—')} | {s.get('total_secs', '—')}s"
            f" | {checks} | {r.get('spent', 0):,} | {r.get('turns', 0)} |"
        )
    return head + "\n".join(lines)


def run_bench(
    ws: Workspace, video_id: str, tasks: list[dict],
    budget_default: int = 90_000, model: str | None = None,
) -> list[dict]:
    """Run every task against the video with a real editor session. Needs Claude auth."""
    import time

    from cutroom.agent.prompts import task_cut, task_highlights
    from cutroom.agent.runner import run_editor_sync
    from cutroom.types import edl_to_dict

    rows = []
    for t in tasks:
        if t.get("n_cuts"):
            prompt = (task_highlights(t["n_cuts"], t.get("vertical", False))
                      + f"\n\nFocus: {t['instruction']}")
        else:
            prompt = task_cut(t["instruction"], t.get("vertical", False))
        started = time.monotonic()
        result = run_editor_sync(
            ws, video_id, prompt,
            budget_chars=t.get("budget", budget_default), model=model,
        )
        edl_dict = edl_to_dict(result.edl) if result.edl else None
        rows.append({
            "task": t["name"],
            "score": score_edl(ws, video_id, edl_dict, t),
            "spent": result.chars_used,
            "turns": result.num_turns,
            "wall_secs": round(time.monotonic() - started, 1),
            "session": result.session_id,
            "editor_ok": result.ok,
        })
    return rows
