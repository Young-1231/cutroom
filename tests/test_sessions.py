"""Session persistence tests — index/state/resolve plus CLI wiring, no Claude calls."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from cutroom.cli import app
from cutroom.sessions import (
    list_sessions,
    load_state,
    record_session,
    resolve_session,
    save_state,
)

VID = "testvid000001"
S1 = "aaaa1111-0000-0000-0000-000000000001"
S2 = "bbbb2222-0000-0000-0000-000000000002"


def add(ws, sid, **over):
    rec = {"session_id": sid, "task": "find the best moment", "turns": 7,
           "spent": 4200, "ok": True, "edl": False}
    rec.update(over)
    record_session(ws, VID, **rec)


def test_record_carries_role(seeded_ws):
    add(seeded_ws, S1, role="scout")
    add(seeded_ws, S2)
    recs = list_sessions(seeded_ws, VID)
    assert recs[0]["role"] == "scout" and recs[1]["role"] == "editor"


def test_record_list_latest_per_session(seeded_ws):
    add(seeded_ws, S1)
    add(seeded_ws, S2, task="a 20s teaser", edl=True, forked=True, resumed_from=S1)
    add(seeded_ws, S1, turns=12, spent=9000)  # resumed run updates in place
    recs = list_sessions(seeded_ws, VID)
    assert [r["session_id"] for r in recs] == [S1, S2]
    assert recs[0]["turns"] == 12 and recs[0]["spent"] == 9000
    assert recs[1]["forked"] is True and recs[1]["resumed_from"] == S1
    assert all(r["ts"] for r in recs)


def test_task_text_is_normalized_and_truncated(seeded_ws):
    add(seeded_ws, S1, task="line\none   " + "x" * 500)
    rec = list_sessions(seeded_ws, VID)[0]
    assert "\n" not in rec["task"] and len(rec["task"]) == 200


def test_resolve_session_prefix(seeded_ws):
    add(seeded_ws, S1)
    add(seeded_ws, S2)
    assert resolve_session(seeded_ws, VID, "aaaa") == S1
    assert resolve_session(seeded_ws, VID, S2) == S2
    with pytest.raises(ValueError, match="matches no session"):
        resolve_session(seeded_ws, VID, "ffff")
    add(seeded_ws, "aaaa9999-0000-0000-0000-000000000003")
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_session(seeded_ws, VID, "aaaa")


def test_state_roundtrip_and_missing(seeded_ws):
    assert load_state(seeded_ws, VID, S1) == []
    save_state(seeded_ws, VID, S1, [12.0, 47.5])
    assert load_state(seeded_ws, VID, S1) == [12.0, 47.5]
    (seeded_ws.sessions_dir(VID) / f"{S2}.json").write_text("{broken")
    assert load_state(seeded_ws, VID, S2) == []


def test_runner_rehydrates_viewed_frames_on_resume(seeded_ws):
    """The evidence gate must honor receipts earned in the parent session."""
    from cutroom.agent.budget import Ledger
    from cutroom.agent.hooks import make_lifecycle_hooks
    from cutroom.agent.tools import make_toolkit

    save_state(seeded_ws, VID, S1, [20.0])
    registry: dict = {"viewed_frames": load_state(seeded_ws, VID, S1)}  # as run_editor does
    make_toolkit(seeded_ws, VID, Ledger(1000), registry)
    assert registry["viewed_frames"] == [20.0]  # setdefault must not clobber

    import asyncio

    hooks = make_lifecycle_hooks(Ledger(1000), registry, seeded_ws.home / "t.jsonl")
    gate = hooks["PreToolUse"][0].hooks[0]
    payload = {"hook_event_name": "PreToolUse", "session_id": S1,
               "tool_name": "mcp__cutroom__mark_moment", "tool_input": {"frame_ts": [20.0]}}
    assert asyncio.run(gate(payload, "tu", {"signal": None})) == {}  # not denied


def test_cli_sessions_list_and_flags(monkeypatch, seeded_ws):
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    runner = CliRunner()

    out = runner.invoke(app, ["sessions", VID])
    assert out.exit_code == 0 and "no sessions yet" in out.output

    add(seeded_ws, S1)
    add(seeded_ws, S2, forked=True, resumed_from=S1, task="vertical recut")
    out = runner.invoke(app, ["sessions", VID])
    assert out.exit_code == 0, out.output
    # cells may wrap at narrow terminal widths, so assert pieces, not the joined string
    assert "aaaa1111" in out.output and "fork of" in out.output

    out = runner.invoke(app, ["ask", VID, "q", "--resume", "aaaa", "--fork", "bbbb"])
    assert out.exit_code == 1
    assert "mutually exclusive" in out.output

    out = runner.invoke(app, ["ask", VID, "q", "--resume", "ffff"])
    assert out.exit_code == 1
    assert "matches no session" in out.output


def test_cli_ask_passes_resolved_session_to_runner(monkeypatch, seeded_ws):
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    from cutroom.agent import runner as runner_mod

    add(seeded_ws, S1)
    seen = {}

    def fake(ws, vid, prompt, budget_chars=0, model=None, resume=None, fork=False, **kw):
        seen.update(resume=resume, fork=fork)
        return runner_mod.EditorResult("answer", None, [], 100, 2, session_id=S2)

    monkeypatch.setattr(runner_mod, "run_editor_sync", fake)
    out = CliRunner().invoke(app, ["ask", VID, "q", "--fork", "aaaa"])
    assert out.exit_code == 0, out.output
    assert seen == {"resume": S1, "fork": True}
    assert f"session {S2[:8]}" in out.output  # the handle for the next --resume/--fork
