"""Shadow-VCS checkpoint tests — snapshot/dedupe/restore/diff, hook mount, CLI verbs."""

from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from cutroom.checkpoints import (
    diff_edls,
    list_checkpoints,
    load_checkpoint,
    restore_checkpoint,
    save_checkpoint,
)
from cutroom.cli import app

VID = "testvid000001"


def edl(cuts, target="landscape", captions=True):
    return {"video_id": VID, "cuts": cuts, "target": target, "captions": captions}


def cut(t0, t1, label=""):
    return {"t0": t0, "t1": t1, "label": label,
            "evidence": {"segment_ids": [1], "frame_ts": [t0 + 1.0], "note": ""}}


EDL_A = edl([cut(10.0, 20.0, "intro")])
EDL_B = edl([cut(10.0, 18.5, "intro"), cut(30.0, 42.0, "payoff")])


def test_save_list_and_dedupe(seeded_ws):
    assert save_checkpoint(seeded_ws, VID, EDL_A, "agent", "s1") == "cp_0001"
    assert save_checkpoint(seeded_ws, VID, EDL_A, "render") is None  # identical → no noise
    assert save_checkpoint(seeded_ws, VID, EDL_B, "render") == "cp_0002"
    cps = list_checkpoints(seeded_ws, VID)
    assert [c.id for c in cps] == ["cp_0001", "cp_0002"]
    assert cps[0].source == "agent" and cps[0].session == "s1" and cps[0].n_cuts == 1
    assert cps[1].n_cuts == 2 and cps[1].total_secs == pytest.approx(20.5)
    assert all(c.ts for c in cps)


def test_load_unknown_checkpoint_raises(seeded_ws):
    with pytest.raises(FileNotFoundError, match="cp_0099"):
        load_checkpoint(seeded_ws, VID, "cp_0099")


def test_restore_snapshots_current_state_first(seeded_ws):
    save_checkpoint(seeded_ws, VID, EDL_A, "agent")
    edl_path = seeded_ws.renders_dir(VID) / "edl.json"
    edl_path.write_text(json.dumps(EDL_B), encoding="utf-8")  # human-edited current

    pre_id, path = restore_checkpoint(seeded_ws, VID, "cp_0001")
    assert path == edl_path
    assert json.loads(edl_path.read_text()) == EDL_A
    assert pre_id == "cp_0002"  # the edited state became a checkpoint: undoable
    assert load_checkpoint(seeded_ws, VID, "cp_0002")["source"] == "pre-restore"

    pre_id2, _ = restore_checkpoint(seeded_ws, VID, "cp_0002")  # undo the restore
    assert json.loads(edl_path.read_text()) == EDL_B
    # Dedupe is HEAD-semantics (vs latest checkpoint only): re-reaching an old state
    # is a new history entry, so the pre-restore snapshot of EDL_A lands as cp_0003.
    assert pre_id2 == "cp_0003"
    assert load_checkpoint(seeded_ws, VID, "cp_0003")["edl"] == EDL_A


def test_restore_moves_corrupt_current_aside(seeded_ws):
    save_checkpoint(seeded_ws, VID, EDL_A, "agent")
    edl_path = seeded_ws.renders_dir(VID) / "edl.json"
    edl_path.write_text("{broken", encoding="utf-8")
    restore_checkpoint(seeded_ws, VID, "cp_0001")
    assert json.loads(edl_path.read_text()) == EDL_A
    assert edl_path.with_suffix(".json.corrupt").read_text() == "{broken"


def test_restore_with_no_current_edl(seeded_ws):
    save_checkpoint(seeded_ws, VID, EDL_A, "agent")
    pre_id, edl_path = restore_checkpoint(seeded_ws, VID, "cp_0001")
    assert pre_id is None and json.loads(edl_path.read_text()) == EDL_A


def test_diff_is_cut_aware():
    assert diff_edls(EDL_A, EDL_A) == []
    lines = diff_edls(EDL_A, EDL_B)
    assert "~ cut 0 [10.00-20.00] -> [10.00-18.50]" in lines
    assert any(line.startswith("+ cut 1 [30.00-42.00]") for line in lines)
    lines = diff_edls(EDL_B, EDL_A)
    assert any(line.startswith("- cut 1") for line in lines)
    lines = diff_edls(EDL_A, edl([cut(10.0, 20.0, "cold open")], target="vertical"))
    assert "~ cut 0 label 'intro' -> 'cold open'" in lines
    assert "~ target landscape -> vertical" in lines


def test_hook_mount_checkpoints_accepted_edl(seeded_ws, tmp_path):
    from cutroom.agent.budget import Ledger
    from cutroom.agent.hooks import make_lifecycle_hooks

    registry = {"viewed_frames": [], "moments": [], "edl": EDL_A}
    trail = tmp_path / "trail.jsonl"
    hooks = make_lifecycle_hooks(
        Ledger(1000), registry, trail,
        on_edl_accepted=lambda e, session: save_checkpoint(seeded_ws, VID, e, "agent", session),
    )
    post = hooks["PostToolUse"][0].hooks[0]
    payload = {"hook_event_name": "PostToolUse", "session_id": "sess-9",
               "tool_name": "mcp__cutroom__propose_edl", "tool_input": {}}
    asyncio.run(post(payload, "tu_1", {"signal": None}))

    cps = list_checkpoints(seeded_ws, VID)
    assert len(cps) == 1 and cps[0].session == "sess-9"
    rec = json.loads(trail.read_text().splitlines()[0])
    assert rec["edl_accepted"] is True and rec["checkpoint"] == "cp_0001"


def test_hook_mount_survives_checkpoint_failure(tmp_path):
    from cutroom.agent.budget import Ledger
    from cutroom.agent.hooks import make_lifecycle_hooks

    def boom(e, session):
        raise OSError("disk full")

    registry = {"viewed_frames": [], "moments": [], "edl": EDL_A}
    trail = tmp_path / "trail.jsonl"
    hooks = make_lifecycle_hooks(Ledger(1000), registry, trail, on_edl_accepted=boom)
    post = hooks["PostToolUse"][0].hooks[0]
    payload = {"hook_event_name": "PostToolUse", "session_id": "s",
               "tool_name": "mcp__cutroom__propose_edl", "tool_input": {}}
    asyncio.run(post(payload, "tu_1", {"signal": None}))  # must not raise
    rec = json.loads(trail.read_text().splitlines()[0])
    assert rec["edl_accepted"] is True and "disk full" in rec["checkpoint_error"]


def test_cli_checkpoints_and_restore(monkeypatch, seeded_ws):
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    save_checkpoint(seeded_ws, VID, EDL_A, "agent")
    (seeded_ws.renders_dir(VID) / "edl.json").write_text(json.dumps(EDL_B), encoding="utf-8")
    runner = CliRunner()

    out = runner.invoke(app, ["checkpoints", VID])
    assert out.exit_code == 0, out.output
    assert "cp_0001" in out.output and "agent" in out.output

    out = runner.invoke(app, ["checkpoints", VID, "--diff", "cp_0001"])
    assert out.exit_code == 0, out.output
    assert "+ cut 1" in out.output

    out = runner.invoke(app, ["restore", VID, "cp_0001"])
    assert out.exit_code == 0, out.output
    assert "restored" in out.output and "cp_0002" in out.output
    assert json.loads((seeded_ws.renders_dir(VID) / "edl.json").read_text()) == EDL_A

    out = runner.invoke(app, ["checkpoints", VID, "--diff", "cp_0001"])
    assert "identical" in out.output

    out = runner.invoke(app, ["restore", VID, "cp_0042"])
    assert out.exit_code == 1
    assert "no checkpoint" in out.output


def test_cli_checkpoints_empty(monkeypatch, seeded_ws):
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    out = CliRunner().invoke(app, ["checkpoints", VID])
    assert out.exit_code == 0
    assert "no checkpoints yet" in out.output
