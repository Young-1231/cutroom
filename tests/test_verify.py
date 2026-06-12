"""Self-critique round: submit_review tool, role surfaces, and the verify/revise flow."""

from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from cutroom.agent.budget import Ledger
from cutroom.agent.runner import _excludes_for_role
from cutroom.agent.tools import make_toolkit
from cutroom.cli import app
from cutroom.types import EDL, Cut, Evidence

VID = "testvid000001"


def call(handler, args):
    return asyncio.run(handler(args))


def text_of(result) -> str:
    return "".join(b["text"] for b in result["content"] if b.get("type") == "text")


@pytest.fixture
def kit(seeded_ws):
    registry: dict = {}
    out = make_toolkit(seeded_ws, VID, Ledger(10_000), registry)
    return {"registry": registry, **out}


def test_submit_review_records_verdicts(kit):
    res = call(kit["handlers"]["submit_review"], {
        "verdicts": [{"cut": 0, "ok": True}, {"cut": 1, "ok": False, "issue": "starts mid-word"}],
        "summary": "one boundary problem",
    })
    assert not res.get("is_error"), text_of(res)
    assert "1 flagged" in text_of(res)
    review = kit["registry"]["review"]
    assert review["summary"] == "one boundary problem"
    assert review["verdicts"][1] == {"cut": 1, "ok": False, "issue": "starts mid-word"}


@pytest.mark.parametrize("args,fragment", [
    ({"verdicts": [], "summary": "x"}, "non-empty"),
    ({"verdicts": "nope", "summary": "x"}, "non-empty"),
    ({"verdicts": [{"cut": 0, "ok": False}], "summary": "x"}, "needs a specific issue"),
    ({"verdicts": [{"ok": True}], "summary": "x"}, "integer cut index"),
])
def test_submit_review_rejects_malformed(kit, args, fragment):
    res = call(kit["handlers"]["submit_review"], args)
    assert res.get("is_error") is True
    assert fragment in text_of(res)


def test_role_tool_surfaces():
    assert _excludes_for_role("editor") == ("submit_review",)
    assert set(_excludes_for_role("scout")) == {"propose_edl", "load_recipe", "submit_review"}
    assert set(_excludes_for_role("critic")) == {"propose_edl", "mark_moment", "load_recipe"}


def test_verify_prompts():
    from cutroom.agent.prompts import task_revise, task_verify

    cuts = [{"t0": 5.0, "t1": 19.8, "label": "bert intro",
             "evidence": {"segment_ids": [1], "frame_ts": [8.0]}}]
    prompt = task_verify(cuts, "make a teaser")
    assert "cut 0: [5.00s–19.80s]" in prompt and "'bert intro'" in prompt
    assert "submit_review" in prompt and "make a teaser" in prompt
    revise = task_revise(["cut 0: starts mid-sentence"])
    assert "cut 0: starts mid-sentence" in revise and "propose_edl" in revise


def _result(runner_mod, edl=None, review=None, session="sess-ed"):
    return runner_mod.EditorResult(
        final_text="done", edl=edl, moments=[], chars_used=100, num_turns=2,
        session_id=session, review=review,
    )


def _edl(label):
    return EDL(video_id=VID, cuts=[Cut(16.0, 28.0, label, Evidence([2], [20.0]))],
               target="landscape", captions=True)


def _run_verify_cut(monkeypatch, seeded_ws, results):
    """Drive `cut --verify --plan` against a scripted run_editor_sync sequence."""
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    from cutroom.agent import runner

    calls = []

    def fake(ws, vid, prompt, **kw):
        calls.append({"prompt": prompt, **kw})
        return results.pop(0)

    monkeypatch.setattr(runner, "run_editor_sync", fake)
    out = CliRunner().invoke(app, ["cut", VID, "tighten it", "--plan", "--verify"])
    return out, calls


def test_verify_clean_review_keeps_edl(monkeypatch, seeded_ws):
    from cutroom.agent import runner

    results = [
        _result(runner, edl=_edl("original")),
        _result(runner, review={"verdicts": [{"cut": 0, "ok": True, "issue": ""}],
                                "summary": "all clean"}, session="sess-critic"),
    ]
    out, calls = _run_verify_cut(monkeypatch, seeded_ws, results)
    assert out.exit_code == 0, out.output
    assert "verify ✓" in out.output and "all clean" in out.output
    assert len(calls) == 2  # no revision round
    assert calls[1]["role"] == "critic"
    assert "cut 0: [16.00s–28.00s]" in calls[1]["prompt"]
    saved = json.loads((seeded_ws.renders_dir(VID) / "edl.json").read_text())
    assert saved["cuts"][0]["label"] == "original"


def test_verify_flagged_issue_triggers_one_revision(monkeypatch, seeded_ws):
    from cutroom.agent import runner

    results = [
        _result(runner, edl=_edl("original")),
        _result(runner, review={"verdicts": [
            {"cut": 0, "ok": False, "issue": "starts mid-sentence"}],
            "summary": "boundary bad"}, session="sess-critic"),
        _result(runner, edl=_edl("revised")),
    ]
    out, calls = _run_verify_cut(monkeypatch, seeded_ws, results)
    assert out.exit_code == 0, out.output
    assert "verify ✗" in out.output and "starts mid-sentence" in out.output
    assert len(calls) == 3
    assert calls[2]["resume"] == "sess-ed"  # revision resumes the editor's session
    assert "REVIEW" in calls[2]["prompt"]
    saved = json.loads((seeded_ws.renders_dir(VID) / "edl.json").read_text())
    assert saved["cuts"][0]["label"] == "revised"


def test_verify_failed_revision_keeps_original(monkeypatch, seeded_ws):
    from cutroom.agent import runner

    results = [
        _result(runner, edl=_edl("original")),
        _result(runner, review={"verdicts": [
            {"cut": 0, "ok": False, "issue": "bad"}], "summary": "s"}),
        _result(runner, edl=None),  # revision came back empty
    ]
    out, _calls = _run_verify_cut(monkeypatch, seeded_ws, results)
    assert out.exit_code == 0, out.output
    assert "keeping the original" in out.output
    saved = json.loads((seeded_ws.renders_dir(VID) / "edl.json").read_text())
    assert saved["cuts"][0]["label"] == "original"
