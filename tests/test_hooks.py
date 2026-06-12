"""Lifecycle hook tests — callbacks invoked directly with synthetic payloads, no Claude."""

from __future__ import annotations

import asyncio
import json

import pytest

from cutroom.agent.budget import Ledger
from cutroom.agent.hooks import (
    DENIED_BUILTINS,
    INVESTIGATION_TOOLS,
    make_lifecycle_hooks,
)
from cutroom.agent.tools import EXHAUSTED_MSG

VIEW = "mcp__cutroom__view_frames"
MARK = "mcp__cutroom__mark_moment"
PROPOSE = "mcp__cutroom__propose_edl"


def call(cb, payload):
    return asyncio.run(cb(payload, "tu_1", {"signal": None}))


def pre(tool, tool_input=None):
    return {"hook_event_name": "PreToolUse", "session_id": "s1",
            "tool_name": tool, "tool_input": tool_input or {}}


def decision(result):
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision")


def trail_records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def env(tmp_path):
    ledger = Ledger(total_chars=10_000)
    registry = {"viewed_frames": [12.0], "moments": [], "edl": None}
    trail = tmp_path / "renders" / "trail.jsonl"
    hooks = make_lifecycle_hooks(ledger, registry, trail)
    cbs = {event: matchers[0].hooks[0] for event, matchers in hooks.items()}
    return {"ledger": ledger, "registry": registry, "trail": trail,
            "hooks": hooks, **cbs}


def test_hook_events_registered(env):
    assert set(env["hooks"]) == {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"}
    for matchers in env["hooks"].values():
        assert matchers[0].matcher is None and len(matchers[0].hooks) == 1


def test_denied_builtins_blocked_and_logged(env):
    for tool in ("Bash", "Write", "WebFetch"):
        assert tool in DENIED_BUILTINS
        result = call(env["PreToolUse"], pre(tool, {"command": "rm -rf /"}))
        assert decision(result) == "deny"
    denies = [r for r in trail_records(env["trail"]) if r["event"] == "deny"]
    assert [d["tool"] for d in denies] == ["Bash", "Write", "WebFetch"]


def test_read_and_cutroom_tools_pass_when_budget_left(env):
    for tool in ("Read", VIEW, MARK):
        payload = pre(tool, {"frame_ts": [12.0]} if tool == MARK else {})
        assert call(env["PreToolUse"], payload) == {}


def test_exhausted_budget_denies_investigation_not_finalize(env):
    env["ledger"].charge("read_transcript", 10_001)
    for tool in INVESTIGATION_TOOLS:
        result = call(env["PreToolUse"], pre(tool))
        assert decision(result) == "deny"
        assert EXHAUSTED_MSG in result["hookSpecificOutput"]["permissionDecisionReason"]
    # finalization stays open so an exhausted session can still conclude
    assert call(env["PreToolUse"], pre(MARK, {"frame_ts": [12.0]})) == {}
    assert call(env["PreToolUse"], pre(PROPOSE, {"cuts": [{"frame_ts": [12.0]}]})) == {}


def test_evidence_gate_denies_unviewed_frame(env):
    result = call(env["PreToolUse"], pre(PROPOSE, {"cuts": [{"frame_ts": [99.0]}]}))
    assert decision(result) == "deny"
    assert "99.00" in result["hookSpecificOutput"]["permissionDecisionReason"]
    result = call(env["PreToolUse"], pre(MARK, {"frame_ts": [12.0, 47.5]}))
    assert decision(result) == "deny"
    assert "47.50" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_evidence_gate_tolerates_near_match(env):
    assert call(env["PreToolUse"], pre(PROPOSE, {"cuts": [{"frame_ts": [12.04]}]})) == {}


def test_evidence_gate_defers_malformed_input_to_handler(env):
    # The handler owns precise argument errors; the gate must not mask them.
    for tool_input in ({"cuts": "nope"}, {"cuts": [{"frame_ts": ["abc"]}]},
                       {"cuts": [{}]}, {}):
        assert call(env["PreToolUse"], pre(PROPOSE, tool_input)) == {}
    assert call(env["PreToolUse"], pre(MARK, {"frame_ts": "nope"})) == {}


def test_post_tool_trail_records_charge_delta(env):
    env["ledger"].charge("get_video_map", 1_200)
    call(env["PostToolUse"], pre("mcp__cutroom__get_video_map"))
    env["ledger"].charge("view_frames", 3_000)
    call(env["PostToolUse"], pre(VIEW))
    recs = [r for r in trail_records(env["trail"]) if r["event"] == "tool"]
    assert [r["charged"] for r in recs] == [1_200, 3_000]
    assert recs[-1]["spent"] == 4_200 and recs[-1]["remaining"] == 5_800
    assert recs[-1]["session"] == "s1" and recs[-1]["tool_use_id"] == "tu_1"


def test_post_tool_trail_marks_edl_accepted(env):
    call(env["PostToolUse"], pre(PROPOSE))
    env["registry"]["edl"] = {"cuts": []}
    call(env["PostToolUse"], pre(PROPOSE))
    recs = [r for r in trail_records(env["trail"]) if r["tool"] == PROPOSE]
    assert "edl_accepted" not in recs[0] and recs[1]["edl_accepted"] is True


def test_failure_and_stop_records(env):
    call(env["PostToolUseFailure"],
         {**pre(VIEW), "hook_event_name": "PostToolUseFailure", "error": "boom"})
    env["registry"]["moments"].append(object())
    call(env["Stop"], {"hook_event_name": "Stop", "session_id": "s1",
                       "stop_hook_active": False})
    recs = trail_records(env["trail"])
    err = next(r for r in recs if r["event"] == "tool_error")
    assert err["tool"] == VIEW and err["error"] == "boom"
    stop = next(r for r in recs if r["event"] == "stop")
    assert stop["total"] == 10_000 and stop["moments"] == 1 and stop["edl"] is False
    assert all("ts" in r for r in recs)


def test_runner_uses_denied_builtins():
    from cutroom.agent import runner

    assert runner.DENIED_BUILTINS is DENIED_BUILTINS
    assert "Bash" in DENIED_BUILTINS and "Read" not in DENIED_BUILTINS
