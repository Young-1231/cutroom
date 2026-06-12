"""Trail aggregation + `cutroom trail` CLI (offline, synthetic trail.jsonl)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from cutroom.cli import app
from cutroom.trail import group_sessions, read_trail, session_records

VID = "testvid000001"


def rec(event, session, **kw):
    return {"ts": kw.pop("ts", "2026-06-12T10:00:00+00:00"),
            "event": event, "session": session, **kw}


RECORDS = [
    rec("tool", "aaaa-1", tool="mcp__cutroom__get_video_map", charged=1400,
        spent=1400, remaining=8600, ts="2026-06-12T10:00:01+00:00"),
    rec("tool", "bbbb-2", tool="mcp__cutroom__search_transcript", charged=300,
        spent=300, remaining=9700, ts="2026-06-12T10:00:02+00:00"),
    rec("deny", "aaaa-1", tool="Read",
        reason="file sandbox: /etc/passwd is outside", ts="2026-06-12T10:00:03+00:00"),
    rec("tool_error", "aaaa-1", tool="mcp__cutroom__view_frames", error="boom",
        ts="2026-06-12T10:00:04+00:00"),
    rec("tool", "aaaa-1", tool="mcp__cutroom__propose_edl", charged=0, spent=1400,
        remaining=8600, edl_accepted=True, checkpoint="cp_0007",
        ts="2026-06-12T10:00:05+00:00"),
    rec("stop", "aaaa-1", spent=1400, total=10_000,
        breakdown={"get_video_map": 1400}, moments=2, edl=True,
        ts="2026-06-12T10:00:06+00:00"),
]


@pytest.fixture
def trail_ws(seeded_ws):
    path = seeded_ws.renders_dir(VID) / "trail.jsonl"
    path.write_text(
        "\n".join(json.dumps(r) for r in RECORDS) + "\nnot json\n", encoding="utf-8"
    )
    return seeded_ws


def test_read_trail_skips_corrupt_lines(trail_ws):
    records = read_trail(trail_ws.renders_dir(VID) / "trail.jsonl")
    assert len(records) == len(RECORDS)


def test_group_sessions_aggregates_interleaved(trail_ws):
    groups = {g.session: g for g in group_sessions(RECORDS)}
    a, b = groups["aaaa-1"], groups["bbbb-2"]
    assert (a.calls, a.denies, a.errors) == (2, 1, 1)
    assert a.spent == 1400 and a.moments == 2 and a.edl is True
    assert a.breakdown == {"get_video_map": 1400}
    assert a.started.endswith("00:01+00:00") and a.ended.endswith("00:06+00:00")
    assert (b.calls, b.denies, b.spent, b.edl) == (1, 0, 300, False)


def test_session_records_prefix_and_ambiguity():
    assert len(session_records(RECORDS, "aaaa")) == 5
    with pytest.raises(ValueError, match="no trail records"):
        session_records(RECORDS, "zzzz")
    ambiguous = RECORDS + [rec("tool", "aab-3")]
    with pytest.raises(ValueError, match="ambiguous"):
        session_records(ambiguous, "aa")


def test_trail_cli_summary(trail_ws, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(trail_ws.home))
    out = CliRunner().invoke(app, ["trail", VID])
    assert out.exit_code == 0, out.output
    assert "aaaa-1" in out.output and "bbbb-2" in out.output
    assert "1,400" in out.output


def test_trail_cli_session_timeline(trail_ws, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(trail_ws.home))
    out = CliRunner().invoke(app, ["trail", VID, "--session", "aaaa"])
    assert out.exit_code == 0, out.output
    assert "get_video_map" in out.output
    assert "✗ deny Read" in out.output and "file sandbox" in out.output
    assert "EDL accepted → cp_0007" in out.output
    assert "■ stop" in out.output and "edl=yes" in out.output


def test_trail_cli_denials_filter(trail_ws, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(trail_ws.home))
    out = CliRunner().invoke(app, ["trail", VID, "--denials"])
    assert out.exit_code == 0, out.output
    assert "✗ Read" in out.output and "outside" in out.output
    assert "get_video_map" not in out.output


def test_trail_cli_empty_is_friendly(seeded_ws, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    out = CliRunner().invoke(app, ["trail", VID])
    assert out.exit_code == 0
    assert "no trail yet" in out.output
