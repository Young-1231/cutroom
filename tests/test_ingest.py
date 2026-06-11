"""Ingest pipeline tests against the synthetic 60s fixture (4 color blocks, spoken audio)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cutroom.db import Workspace
from cutroom.ingest import detect_audio_events, detect_shots, fetch, log_footage, transcribe

KEYWORDS = ("zebra", "quantum", "volcano", "harvest")


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(home=tmp_path / "cutroom-home")
    yield w
    w.close()


@pytest.fixture(scope="session")
def ingested(synthetic_video, tmp_path_factory: pytest.TempPathFactory) -> tuple[Workspace, str]:
    """One shared fetch of the synthetic video for the analysis-stage tests."""
    w = Workspace(home=tmp_path_factory.mktemp("ingested-home"))
    meta = fetch(str(synthetic_video["path"]), w)
    yield w, meta.id
    w.close()


@pytest.mark.slow
def test_fetch_local_file(synthetic_video, ws: Workspace):
    meta = fetch(str(synthetic_video["path"]), ws)

    assert abs(meta.duration - 60.0) <= 1.0
    assert meta.title == "synthetic"
    assert meta.width == 640 and meta.height == 360
    assert ws.source_path(meta.id).exists()
    assert ws.audio_path(meta.id).exists()

    stored = ws.get_video(meta.id)
    assert stored is not None
    assert stored.source == str(synthetic_video["path"])

    # Idempotent: re-run must reuse existing media files, not rebuild them.
    mtimes = (ws.source_path(meta.id).stat().st_mtime_ns,
              ws.audio_path(meta.id).stat().st_mtime_ns)
    meta2 = fetch(str(synthetic_video["path"]), ws)
    assert meta2.id == meta.id
    assert (ws.source_path(meta.id).stat().st_mtime_ns,
            ws.audio_path(meta.id).stat().st_mtime_ns) == mtimes


@pytest.mark.slow
def test_detect_shots_boundaries(ingested):
    ws, vid = ingested
    shots = detect_shots(ws, vid)
    duration = ws.get_video(vid).duration

    # Contiguous, monotonic, full [0, duration] coverage.
    assert shots[0].t0 == 0.0
    assert abs(shots[-1].t1 - duration) < 0.5
    for prev, cur in zip(shots, shots[1:], strict=False):
        assert prev.t1 == cur.t0
    assert all(s.t1 > s.t0 for s in shots)

    inner = [s.t1 for s in shots[:-1]]
    assert len(inner) >= 3
    for target in (15.0, 30.0, 45.0):
        assert any(abs(b - target) <= 2.0 for b in inner), (target, inner)

    assert len(ws.get_shots(vid)) == len(shots)


@pytest.mark.slow
def test_transcribe_tiny(ingested, synthetic_video):
    if not synthetic_video["has_speech"]:
        pytest.skip("fixture has no speech (no `say` on this machine)")
    ws, vid = ingested
    segments = transcribe(ws, vid, model_size="tiny")

    text = " ".join(s.text for s in segments).lower()
    hits = sum(1 for kw in KEYWORDS if kw in text)
    assert hits >= 2, f"only {hits} keywords recognized in: {text!r}"

    assert any(s.words for s in segments)
    assert all(s.id is not None for s in segments)
    assert len(ws.get_segments(vid)) == len(segments)


@pytest.mark.slow
def test_detect_audio_events_silences(ingested, synthetic_video):
    if not synthetic_video["has_speech"]:
        pytest.skip("sine-tone fixture has no silent spans")
    ws, vid = ingested
    events = detect_audio_events(ws, vid)

    silences = [e for e in events if e.kind == "silence"]
    assert len(silences) >= 1  # apad tail of each block is silent
    assert all(e.t1 > e.t0 and e.value == -60.0 for e in silences)
    assert len(ws.get_audio_events(vid)) == len(events)


@pytest.mark.slow
def test_log_footage_end_to_end(synthetic_video, ws: Workspace):
    steps: list[str] = []
    # cutroom.index may not exist yet; log_footage must not raise either way.
    meta = log_footage(str(synthetic_video["path"]), ws,
                       model_size="tiny", on_step=steps.append)

    assert ws.get_video(meta.id) is not None
    assert len(ws.get_shots(meta.id)) >= 1
    if synthetic_video["has_speech"]:
        assert len(ws.get_segments(meta.id)) >= 1
    assert steps[0] == "fetch"
    assert {"shots", "transcribe", "audio", "scenes"} <= set(steps)
