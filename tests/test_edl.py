"""Pure-logic tests for EDL validation and snapping against seeded_ws data."""

import math

from cutroom.render.edl import snap_edl, validate_edl
from cutroom.types import EDL, Cut, Evidence

VID = "testvid000001"
DUR = 120.0


def _evidence(seg_id: int, frame_ts: list[float]) -> Evidence:
    return Evidence(segment_ids=[seg_id], frame_ts=frame_ts, note="checked it")


def _valid_edl(ws) -> EDL:
    segs = ws.get_segments(VID)
    return EDL(
        video_id=VID,
        cuts=[
            Cut(16.0, 28.0, "submarines", _evidence(segs[1].id, [20.0])),
            Cut(46.0, 58.0, "new species", _evidence(segs[3].id, [50.0])),
        ],
    )


def test_valid_edl_passes(seeded_ws):
    assert validate_edl(_valid_edl(seeded_ws), DUR) == []


def test_no_cuts(seeded_ws):
    errors = validate_edl(EDL(video_id=VID, cuts=[]), DUR)
    assert errors and "no cuts" in errors[0]


def test_not_sorted(seeded_ws):
    edl = _valid_edl(seeded_ws)
    edl.cuts.reverse()
    assert any("sorted" in e for e in validate_edl(edl, DUR))


def test_overlapping(seeded_ws):
    segs = seeded_ws.get_segments(VID)
    edl = EDL(
        video_id=VID,
        cuts=[
            Cut(16.0, 28.0, "", _evidence(segs[1].id, [20.0])),
            Cut(25.0, 40.0, "", _evidence(segs[2].id, [32.0])),
        ],
    )
    assert any("overlap" in e for e in validate_edl(edl, DUR))


def test_out_of_bounds(seeded_ws):
    segs = seeded_ws.get_segments(VID)
    for t0, t1 in [(-1.0, 10.0), (115.0, 125.0)]:
        edl = EDL(video_id=VID, cuts=[Cut(t0, t1, "", _evidence(segs[0].id, [t0 + 1.0]))])
        assert any("out of bounds" in e for e in validate_edl(edl, DUR)), (t0, t1)


def test_length_limits(seeded_ws):
    segs = seeded_ws.get_segments(VID)
    short = EDL(video_id=VID, cuts=[Cut(16.0, 16.5, "", _evidence(segs[1].id, [16.2]))])
    assert any("length" in e for e in validate_edl(short, DUR))
    long = EDL(video_id=VID, cuts=[Cut(0.0, 250.0, "", _evidence(segs[0].id, [5.0]))])
    assert any("length" in e for e in validate_edl(long, 300.0))


def test_evidence_required(seeded_ws):
    segs = seeded_ws.get_segments(VID)
    no_segments = EDL(
        video_id=VID,
        cuts=[Cut(16.0, 28.0, "", Evidence(segment_ids=[], frame_ts=[20.0]))],
    )
    assert any("segment_ids" in e for e in validate_edl(no_segments, DUR))
    far_frame = EDL(
        video_id=VID,
        cuts=[Cut(16.0, 28.0, "", Evidence(segment_ids=[segs[1].id], frame_ts=[40.0]))],
    )
    assert any("frame" in e for e in validate_edl(far_frame, DUR))
    bare = EDL(video_id=VID, cuts=[Cut(16.0, 28.0, "", Evidence())])
    assert validate_edl(bare, DUR, require_evidence=False) == []
    assert validate_edl(bare, DUR) != []


# --- snapping -----------------------------------------------------------


def test_snap_moves_mid_word_edge_to_boundary(seeded_ws):
    # Segment 2 spans 16-28 with 9 evenly spaced words: boundaries at 16.0, 17.333, ...
    segs = seeded_ws.get_segments(VID)
    edl = EDL(
        video_id=VID,
        cuts=[Cut(16.2, 27.9, "", _evidence(segs[1].id, [20.0]))],
    )
    out = snap_edl(edl, segs)
    # 16.2 is 0.2s into the first word -> snap to 16.0, then pad 0.12 outward.
    assert math.isclose(out.cuts[0].t0, 16.0 - 0.12, abs_tol=1e-6)
    # 27.9 is 0.1s before the last word ends -> snap to 28.0, then pad outward.
    assert math.isclose(out.cuts[0].t1, 28.0 + 0.12, abs_tol=1e-6)
    # Original EDL is untouched.
    assert edl.cuts[0].t0 == 16.2 and edl.cuts[0].t1 == 27.9


def test_snap_leaves_far_or_outside_edges(seeded_ws):
    segs = seeded_ws.get_segments(VID)
    edl = EDL(
        video_id=VID,
        # 16.6 is mid-word but >0.3s from both boundaries; 14.0 is in a silence.
        cuts=[Cut(14.0, 16.6, "", _evidence(segs[1].id, [15.0]))],
    )
    out = snap_edl(edl, segs)
    assert math.isclose(out.cuts[0].t0, 14.0 - 0.12, abs_tol=1e-6)
    assert math.isclose(out.cuts[0].t1, 16.6 + 0.12, abs_tol=1e-6)


def test_snap_never_creates_overlaps(seeded_ws):
    segs = seeded_ws.get_segments(VID)
    edl = EDL(
        video_id=VID,
        cuts=[
            Cut(16.0, 28.0, "", _evidence(segs[1].id, [20.0])),
            Cut(28.1, 40.0, "", _evidence(segs[2].id, [32.0])),
        ],
    )
    out = snap_edl(edl, segs)
    assert out.cuts[0].t1 <= out.cuts[1].t0
    assert validate_edl(out, DUR) == []


def test_snap_clamps_to_content_bounds(seeded_ws):
    segs = seeded_ws.get_segments(VID)
    edl = EDL(video_id=VID, cuts=[Cut(0.05, 118.0, "", _evidence(segs[0].id, [5.0]))])
    out = snap_edl(edl, segs)
    assert out.cuts[0].t0 >= 0.0
    assert out.cuts[0].t1 <= 118.0  # last known content end, never padded past it


def test_snap_drops_degenerate_and_clamps_to_duration(seeded_ws):
    from cutroom.types import Cut, Evidence
    segs = seeded_ws.get_segments(VID)
    # Two heavily overlapping cuts: midpoint resolution must not invert either, and a
    # cut that collapses to non-positive length must be dropped, never emitted.
    edl = EDL(video_id=VID, cuts=[
        Cut(10.0, 40.0, "a", Evidence(segment_ids=[3], frame_ts=[20.0])),
        Cut(12.0, 13.0, "b", Evidence(segment_ids=[3], frame_ts=[12.5])),
    ])
    out = snap_edl(edl, segs, duration=DUR)
    for c in out.cuts:
        assert c.t1 > c.t0
        assert c.t1 <= DUR + 1e-6
    # Sorted, non-overlapping.
    for prev, cur in zip(out.cuts, out.cuts[1:], strict=False):
        assert cur.t0 >= prev.t1 - 1e-9


def test_snap_respects_real_duration_over_segment_overrun(seeded_ws):
    from cutroom.types import Cut, Evidence, Word
    # An ASR segment whose end overruns the media end must not let a cut pad past EOF.
    segs = seeded_ws.get_segments(VID)
    segs[-1].t1 = DUR + 0.5
    segs[-1].words = [Word("end", DUR - 0.2, DUR + 0.5)]
    edl = EDL(video_id=VID, cuts=[Cut(100.0, DUR, "x", Evidence([8], [110.0]))])
    out = snap_edl(edl, segs, duration=DUR)
    assert out.cuts[0].t1 <= DUR + 1e-6
