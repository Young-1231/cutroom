"""Scene building + video map rendering against the seeded workspace."""

from cutroom.index.map import build_and_store_scenes, build_scenes, fmt_ts, render_video_map

VID = "testvid000001"


def _inputs(ws):
    return ws.get_shots(VID), ws.get_segments(VID), ws.get_audio_events(VID)


def test_build_scenes_splits_at_silences(seeded_ws):
    shots, segments, events = _inputs(seeded_ws)
    scenes = build_scenes(shots, segments, events, VID, 120.0)
    assert len(scenes) == 4  # three >=2s silences -> four scenes
    assert scenes[0].t0 == 0.0 and scenes[-1].t1 == 120.0
    for a, b in zip(scenes, scenes[1:], strict=False):
        assert a.t1 == b.t0
    bounds = [s.t1 for s in scenes[:-1]]
    for boundary, silence_mid in zip(bounds, [14.5, 44.5, 89.5], strict=True):
        assert abs(boundary - silence_mid) <= 1.5
    assert all(s.title for s in scenes)


def test_build_scenes_respects_max_length(seeded_ws):
    shots, segments, events = _inputs(seeded_ws)
    scenes = build_scenes(shots, segments, events, VID, 120.0, max_scene_seconds=20.0)
    assert all(s.t1 - s.t0 <= 20.0 + 1e-6 for s in scenes)


def test_build_and_store_scenes_with_summarizer(seeded_ws):
    stored = build_and_store_scenes(seeded_ws, VID, summarizer=lambda text: "ONE-LINER")
    assert stored and all(s.id is not None for s in stored)
    assert all(s.summary == "ONE-LINER" for s in stored if "(no speech)" not in s.summary)
    assert seeded_ws.get_scenes(VID) == stored


def test_render_video_map_compact(seeded_ws):
    text = render_video_map(seeded_ws, VID)
    assert "Going deep" in text and "New species" in text
    assert "[00:00–00:43]" in text
    assert "silences:" in text
    assert "speech" in text
    assert len(text) < 2500


def test_fmt_ts():
    assert fmt_ts(0) == "00:00"
    assert fmt_ts(75) == "01:15"
    assert fmt_ts(3700) == "1:01:40"


def test_build_scenes_adaptive_on_continuous_speech():
    """Narrated footage with only weak pauses must split at the strongest pauses,
    not degenerate into equal max-length splits."""
    from cutroom.types import Segment

    vid = "contvid000001"
    spans, t = [], 0.0
    for gap_after, length in [(0.2, 60.4), (1.2, 69.2), (0.2, 80.35), (0.9, 60.0),
                              (1.5, 88.0), (0.2, 38.0)]:
        spans.append((t, t + length))
        t += length + gap_after
    duration = t
    segments = [Segment(None, vid, a, b, f"segment from {a:.0f}") for a, b in spans]

    scenes = build_scenes([], segments, [], vid, duration)
    bounds = [s.t1 for s in scenes[:-1]]
    # target = round(~330/90) = 4 scenes -> the 3 strongest pauses win, weakest (0.2s) never used
    assert len(scenes) == 4
    gap_mids = [130.4, 272.0, 361.2]  # midpoints of the 1.2s, 0.9s and 1.5s pauses
    for b in bounds:
        assert any(abs(b - g) < 2.0 for g in gap_mids), bounds
    # no arbitrary equal-split boundary (old behavior would cut at duration/2 etc.)
    assert all(s.t1 - s.t0 <= 180.0 + 1e-6 for s in scenes)
