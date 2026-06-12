"""Mechanical scorecard logic (cutroom.bench) — offline, seeded workspace."""

from __future__ import annotations

import json

import pytest

from cutroom.bench import (
    boundary_distances,
    load_tasks,
    markdown_table,
    natural_edges,
    score_edl,
)

VID = "testvid000001"


def cut(t0, t1, segs=(2,), frames=(20.0,)):
    return {"t0": t0, "t1": t1, "label": "",
            "evidence": {"segment_ids": list(segs), "frame_ts": list(frames)}}


def test_natural_edges_collects_segment_and_silence_bounds(seeded_ws):
    edges = natural_edges(seeded_ws, VID)
    assert edges == sorted(edges)
    assert len(edges) > 4


def test_boundary_distances(seeded_ws):
    edges = natural_edges(seeded_ws, VID)
    snapped = {"cuts": [cut(edges[0], edges[1])]}
    assert boundary_distances(edges, snapped) == [0.0, 0.0]
    off = {"cuts": [cut(edges[0] + 3.33, edges[1])]}
    dists = boundary_distances(edges, off)
    assert dists[0] > 0.5


def test_score_edl_no_edl():
    score = score_edl(None, VID, None, {})
    assert score == {"ok": False, "produced": False}


def test_score_edl_passes_when_constraints_met(seeded_ws):
    edges = natural_edges(seeded_ws, VID)
    t0, t1 = edges[1], edges[3]
    spec = {"target_secs": t1 - t0, "tolerance": 0.25}
    score = score_edl(seeded_ws, VID, {"cuts": [cut(t0, t1)]}, spec)
    assert score["ok"] is True, score
    assert score["checks"] == {"duration": True, "receipts": True, "boundaries": True}
    assert score["boundary_max"] == 0.0


def test_score_edl_flags_violations(seeded_ws):
    edges = natural_edges(seeded_ws, VID)
    bad = {"cuts": [
        {"t0": edges[0] + 1.7, "t1": edges[2], "label": "",
         "evidence": {"segment_ids": [], "frame_ts": []}},  # no receipts, off-edge
    ]}
    spec = {"target_secs": 5, "tolerance": 0.1, "n_cuts": 2}
    score = score_edl(seeded_ws, VID, bad, spec)
    assert score["ok"] is False
    assert score["checks"]["receipts"] is False
    assert score["checks"]["n_cuts"] is False
    assert score["checks"]["boundaries"] is False


def test_load_tasks_validates(tmp_path):
    good = tmp_path / "tasks.json"
    good.write_text(json.dumps([{"name": "a", "instruction": "do"}]))
    assert load_tasks(good)[0]["name"] == "a"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"instruction": "no name"}]))
    with pytest.raises(ValueError, match="expected a list"):
        load_tasks(bad)


def test_shipped_task_spec_parses():
    from pathlib import Path

    tasks = load_tasks(Path(__file__).parent.parent / "bench" / "repurpose.json")
    assert {t["name"] for t in tasks} == {"teaser-30s", "highlights-3", "vertical-short-20s"}


def test_markdown_table_renders_both_outcomes():
    rows = [
        {"task": "t1", "spent": 9000, "turns": 12,
         "score": {"ok": True, "produced": True, "n_cuts": 2, "total_secs": 29.5,
                   "checks": {"duration": True, "receipts": True}}},
        {"task": "t2", "spent": 0, "turns": 3, "score": {"ok": False, "produced": False}},
    ]
    table = markdown_table(rows)
    assert "| t1 | ✅ | 2 | 29.5s | ✓ duration, ✓ receipts | 9,000 | 12 |" in table
    assert "no EDL produced" in table
