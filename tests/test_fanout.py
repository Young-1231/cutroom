"""Fan-out orchestration: chunking + global dedupe/rank + merge (offline)."""

import anyio

from cutroom.agent import fanout
from cutroom.agent.runner import EditorResult
from cutroom.types import Evidence, Moment, Scene

VID = "testvid000001"


def _scenes(bounds):
    return [Scene(None, VID, a, b, f"s{i}", "") for i, (a, b) in enumerate(bounds)]


def test_chunk_scenes_groups_to_target_windows():
    # 1200s of 100s scenes, ~360s target -> ceil-ish 3 windows, scene-aligned, full cover.
    scenes = _scenes([(i * 100.0, i * 100.0 + 100.0) for i in range(12)])
    windows = fanout.chunk_scenes(scenes, 1200.0, target_seconds=360.0)
    assert 2 <= len(windows) <= 4
    assert windows[0][0] == 0.0 and windows[-1][1] == 1200.0
    for prev, cur in zip(windows, windows[1:], strict=False):
        assert prev[1] == cur[0]  # contiguous, no gaps/overlaps


def test_chunk_scenes_caps_at_max_windows():
    scenes = _scenes([(i * 100.0, i * 100.0 + 100.0) for i in range(100)])
    windows = fanout.chunk_scenes(scenes, 10_000.0, target_seconds=60.0, max_windows=8)
    assert len(windows) == 8
    assert windows[-1][1] == 10_000.0


def test_chunk_scenes_empty_and_short():
    assert fanout.chunk_scenes([], 0.0) == []
    one = fanout.chunk_scenes(_scenes([(0.0, 50.0)]), 50.0)
    assert one == [(0.0, 50.0)]


def test_dedupe_rank_takes_top_n_non_overlapping():
    ms = [
        Moment(0.0, 10.0, "a", Evidence([1], [5.0]), score=0.4),
        Moment(5.0, 15.0, "b", Evidence([2], [9.0]), score=0.9),   # overlaps a, higher
        Moment(40.0, 50.0, "c", Evidence([3], [45.0]), score=0.7),
        Moment(60.0, 70.0, "d", Evidence([4], [65.0]), score=0.5),
    ]
    top = fanout.dedupe_rank_moments(ms, n=2)
    assert [m.reason for m in top] == ["b", "c"]  # top-2 by score, no overlap, chronological
    # b (0.9) beats overlapping a (0.4); next is c (0.7); returned sorted by t0
    assert top[0].t0 < top[1].t0


def test_dedupe_rank_skips_overlap_with_kept():
    ms = [
        Moment(0.0, 10.0, "x", Evidence([1], [5.0]), score=0.9),
        Moment(8.0, 18.0, "y", Evidence([2], [12.0]), score=0.8),  # overlaps x -> skipped
        Moment(20.0, 30.0, "z", Evidence([3], [25.0]), score=0.7),
    ]
    top = fanout.dedupe_rank_moments(ms, n=3)
    assert [m.reason for m in top] == ["x", "z"]


def test_highlights_fanout_merges_scout_results(monkeypatch, seeded_ws):
    """Each window's scout returns moments; orchestrator merges into one EDL."""
    # seeded_ws video is 120s with 3 scenes; force 2 windows.
    monkeypatch.setattr(fanout, "chunk_scenes", lambda *a, **k: [(0.0, 60.0), (60.0, 120.0)])

    calls = []

    async def fake_run_editor(ws, vid, prompt, budget_chars=0, model=None, role="editor"):
        calls.append(prompt)
        assert role == "scout"  # scouts must run with the propose_edl-stripped toolkit
        if "00:00" in prompt:  # first window
            m = Moment(16.0, 28.0, "submarine depth", Evidence([2], [20.0]), score=0.6)
        else:  # second window
            m = Moment(91.0, 103.0, "mariana plan", Evidence([7], [95.0]), score=0.9)
        return EditorResult("scouted", None, [m], chars_used=1000, num_turns=4, ok=True)

    monkeypatch.setattr(fanout, "run_editor", fake_run_editor)

    res = anyio.run(fanout.highlights_fanout, seeded_ws, VID, 2, False)
    assert len(calls) == 2  # one scout per window, run concurrently
    assert res.ok and res.edl is not None
    assert res.chars_used == 2000 and res.num_turns == 8
    # both moments kept, ordered chronologically, evidence preserved
    assert [c.t0 for c in res.edl.cuts] == [16.0, 91.0]
    assert res.edl.cuts[1].evidence.frame_ts == [95.0]
    assert res.edl.target == "landscape"


def test_highlights_fanout_all_windows_fail(monkeypatch, seeded_ws):
    monkeypatch.setattr(fanout, "chunk_scenes", lambda *a, **k: [(0.0, 120.0)])

    async def empty_scout(ws, vid, prompt, budget_chars=0, model=None, role="editor"):
        return EditorResult("none", None, [], chars_used=10, num_turns=1, ok=False, error="x")

    monkeypatch.setattr(fanout, "run_editor", empty_scout)
    res = anyio.run(fanout.highlights_fanout, seeded_ws, VID, 3, True)
    assert res.ok is False
    assert res.edl is None
