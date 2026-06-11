"""Agent toolkit tests — handlers invoked directly as async functions, no Claude calls."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys

import pytest

from cutroom.agent.budget import Ledger
from cutroom.agent.tools import EXHAUSTED_MSG, make_toolkit

VID = "testvid000001"
HAS_FFMPEG = shutil.which("ffmpeg") is not None


def call(handler, args=None):
    return asyncio.run(handler(args or {}))


def text_of(result) -> str:
    return "".join(b["text"] for b in result["content"] if b.get("type") == "text")


def cut(t0, t1, segs, frames, label=""):
    return {"t0": t0, "t1": t1, "label": label, "segment_ids": segs, "frame_ts": frames}


@pytest.fixture
def kit(seeded_ws):
    ledger = Ledger(total_chars=50_000)
    registry: dict = {}
    out = make_toolkit(seeded_ws, VID, ledger, registry)
    return {"ws": seeded_ws, "ledger": ledger, "registry": registry, **out}


def test_toolkit_shape(kit):
    assert kit["tool_names"] == [
        f"mcp__cutroom__{n}"
        for n in ["get_video_map", "search_transcript", "read_transcript", "view_frames",
                  "probe_audio", "mark_moment", "propose_edl"]
    ]
    assert kit["registry"] == {"viewed_frames": [], "moments": [], "edl": None}


def test_get_video_map_contains_scene_titles(kit):
    pytest.importorskip("cutroom.index.map")
    text = text_of(call(kit["handlers"]["get_video_map"]))
    assert "Going deep" in text
    assert "New species" in text
    assert text.endswith(kit["ledger"].line())


def test_get_video_map_stub_when_index_missing(kit, monkeypatch):
    monkeypatch.setitem(sys.modules, "cutroom.index.map", None)  # force ImportError
    text = text_of(call(kit["handlers"]["get_video_map"]))
    assert "unavailable" in text
    assert text.endswith(kit["ledger"].line())
    assert kit["ledger"].breakdown.get("get_video_map", 0) > 0


def test_search_transcript_charges_budget_and_cites(kit):
    res = call(kit["handlers"]["search_transcript"], {"query": "submarines"})
    text = text_of(res)
    assert "submarines" in text
    assert "[00:16" in text  # mm:ss stamp of the matching segment
    assert "seg" in text
    assert kit["ledger"].breakdown.get("search_transcript", 0) > 0
    assert text.endswith(kit["ledger"].line())


def test_read_transcript_charges_actual_chars(kit):
    res = call(kit["handlers"]["read_transcript"], {"t0": 0.0, "t1": 30.0})
    text = text_of(res)
    assert "welcome everyone" in text
    # text = payload + "\n" + ledger.line(); only the payload is charged
    charged = kit["ledger"].breakdown["read_transcript"]
    assert charged == len(text) - len(kit["ledger"].line()) - 1


def test_read_transcript_caps_to_remaining_budget(seeded_ws):
    ledger = Ledger(total_chars=200)
    kit = make_toolkit(seeded_ws, VID, ledger, {})
    res = call(kit["handlers"]["read_transcript"], {"t0": 0.0, "t1": 120.0})
    payload = text_of(res).rsplit("\n", 1)[0]
    assert len(payload) <= 200


@pytest.mark.slow
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_view_frames_extracts_jpegs_and_registers(kit):
    ws = kit["ws"]
    src = ws.source_path(VID)
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:size=320x240:duration=5:rate=24", "-c:v", "libx264",
         "-preset", "ultrafast", str(src)],
        check=True, capture_output=True,
    )
    res = call(kit["handlers"]["view_frames"], {"timestamps": [1.0, 2.5]})
    images = [b for b in res["content"] if b.get("type") == "image"]
    assert len(images) == 2
    assert all(b["mimeType"] == "image/jpeg" and b["data"] for b in images)
    assert len(list(ws.frames_dir(VID).glob("*.jpg"))) == 2
    assert kit["registry"]["viewed_frames"] == [1.0, 2.5]
    assert kit["ledger"].breakdown["view_frames"] == 2 * Ledger.FRAME_COST
    assert text_of(res).endswith(kit["ledger"].line())
    # more than 6 timestamps are capped at 6 per call
    res2 = call(kit["handlers"]["view_frames"],
                {"timestamps": [0.1, 0.6, 1.1, 1.6, 2.1, 2.6, 3.1]})
    assert len([b for b in res2["content"] if b.get("type") == "image"]) == 6
    assert len(kit["registry"]["viewed_frames"]) == 8


def test_view_frames_errors_when_source_missing(kit):
    res = call(kit["handlers"]["view_frames"], {"timestamps": [1.0]})
    assert res.get("is_error") is True
    assert "source media missing" in text_of(res)
    assert kit["registry"]["viewed_frames"] == []


def test_probe_audio_reports_events_and_density(kit):
    text = text_of(call(kit["handlers"]["probe_audio"], {"t0": 10.0, "t1": 20.0}))
    # seg1 overlaps 3s, seg2 overlaps 4s -> 7s speech in a 10s span
    assert "70%" in text
    assert "silence" in text
    assert kit["ledger"].breakdown["probe_audio"] > 0
    assert text.endswith(kit["ledger"].line())


def test_mark_moment_rejects_unviewed_frames(kit):
    res = call(kit["handlers"]["mark_moment"],
               {"t0": 16.0, "t1": 28.0, "reason": "depth claim",
                "segment_ids": [2], "frame_ts": [20.0]})
    assert res.get("is_error") is True
    assert "never viewed" in text_of(res)
    assert kit["registry"]["moments"] == []


def test_mark_moment_rejects_frames_outside_window(kit):
    kit["registry"]["viewed_frames"].append(50.0)
    res = call(kit["handlers"]["mark_moment"],
               {"t0": 16.0, "t1": 28.0, "reason": "depth claim",
                "segment_ids": [2], "frame_ts": [50.0]})
    assert res.get("is_error") is True
    assert "outside" in text_of(res)
    assert kit["registry"]["moments"] == []


def test_mark_moment_accepts_viewed_frames(kit):
    kit["registry"]["viewed_frames"].extend([17.0, 27.5])
    res = call(kit["handlers"]["mark_moment"],
               {"t0": 16.0, "t1": 28.0, "reason": "depth claim",
                "segment_ids": [2], "frame_ts": [17.0, 27.5], "score": 0.9})
    assert not res.get("is_error"), text_of(res)
    (m,) = kit["registry"]["moments"]
    assert (m.t0, m.t1, m.score) == (16.0, 28.0, 0.9)
    assert m.evidence.segment_ids == [2]
    assert m.evidence.frame_ts == [17.0, 27.5]


def test_propose_edl_basic_validation_catches_overlap(kit, monkeypatch):
    monkeypatch.setitem(sys.modules, "cutroom.render.edl", None)  # force basic path
    res = call(kit["handlers"]["propose_edl"],
               {"cuts": [cut(1.0, 10.0, [1], [5.0]), cut(8.0, 20.0, [2], [15.0])]})
    assert res.get("is_error") is True
    assert "overlap" in text_of(res)
    assert kit["registry"]["edl"] is None


def test_propose_edl_basic_validation_bounds_length_empty(kit, monkeypatch):
    monkeypatch.setitem(sys.modules, "cutroom.render.edl", None)
    res = call(kit["handlers"]["propose_edl"],
               {"cuts": [cut(0.0, 0.5, [1], [0.2]), cut(100.0, 130.0, [8], [110.0])]})
    text = text_of(res)
    assert res.get("is_error") is True
    assert "length" in text
    assert "bounds" in text
    assert "at least one cut" in text_of(call(kit["handlers"]["propose_edl"], {"cuts": []}))


def test_propose_edl_accepts_valid_cuts(kit, monkeypatch):
    monkeypatch.setitem(sys.modules, "cutroom.render.edl", None)
    res = call(kit["handlers"]["propose_edl"],
               {"cuts": [cut(1.0, 13.0, [1], [5.0], "intro"),
                         cut(16.0, 28.0, [2], [20.0], "depth")],
                "target": "vertical", "captions": False})
    assert not res.get("is_error"), text_of(res)
    edl = kit["registry"]["edl"]
    assert edl["video_id"] == VID
    assert edl["target"] == "vertical"
    assert edl["captions"] is False
    assert [c["t0"] for c in edl["cuts"]] == [1.0, 16.0]
    assert edl["cuts"][0]["evidence"]["segment_ids"] == [1]
    assert edl["cuts"][1]["evidence"]["frame_ts"] == [20.0]


def test_exhausted_budget_flips_investigation_tools_to_finalize(kit):
    kit["ledger"].charge("setup", 10**6)
    assert kit["ledger"].exhausted
    args = {"query": "x", "t0": 0.0, "t1": 5.0, "timestamps": [1.0]}
    for name in ["get_video_map", "search_transcript", "read_transcript",
                 "view_frames", "probe_audio"]:
        assert text_of(call(kit["handlers"][name], args)) == EXHAUSTED_MSG
    # finalization tools still work
    kit["registry"]["viewed_frames"].append(20.0)
    res = call(kit["handlers"]["mark_moment"],
               {"t0": 16.0, "t1": 28.0, "reason": "r", "segment_ids": [2], "frame_ts": [20.0]})
    assert not res.get("is_error")
    assert len(kit["registry"]["moments"]) == 1


def test_prompts_contracts():
    from cutroom.agent.prompts import (
        EDITOR_SYSTEM,
        task_ask,
        task_chapters,
        task_cut,
        task_highlights,
    )

    assert "get_video_map" in EDITOR_SYSTEM
    assert "propose_edl" in EDITOR_SYSTEM
    assert "NEVER" in EDITOR_SYSTEM
    hl = task_highlights(3, vertical=True)
    assert "3" in hl and "vertical" in hl
    ask = task_ask("how deep can submarines go?")
    assert "how deep can submarines go?" in ask and "mm:ss" in ask and "NOT" in ask
    assert "mm:ss" in task_chapters() and "NOT" in task_chapters()
    assert "trim the intro" in task_cut("trim the intro", vertical=False)


def test_runner_module_shape():
    from cutroom.agent.runner import EditorResult, run_editor, run_editor_sync

    assert callable(run_editor) and callable(run_editor_sync)
    r = EditorResult(final_text="", edl=None, moments=[], chars_used=0, num_turns=0)
    assert r.edl is None and r.moments == []
