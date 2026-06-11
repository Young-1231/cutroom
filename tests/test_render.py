"""End-to-end render tests on the synthetic 60s fixture (slow, media-heavy)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cutroom.db import Workspace
from cutroom.render.ffmpeg import render_edl, render_reel
from cutroom.render.receipts import write_receipts
from cutroom.types import EDL, Cut, Evidence, Moment, Segment, VideoMeta, Word

pytestmark = pytest.mark.slow

VID = "rendervid0001"


def _seg(t0: float, t1: float, text: str) -> Segment:
    words = text.split()
    step = (t1 - t0) / len(words)
    return Segment(
        id=None, video_id=VID, t0=t0, t1=t1, text=text,
        words=[Word(w, t0 + i * step, t0 + (i + 1) * step) for i, w in enumerate(words)],
    )


def _setup(tmp_path: Path, synthetic_video: dict) -> tuple[Workspace, list[Cut]]:
    ws = Workspace(home=tmp_path / "cutroom-home")
    shutil.copy(synthetic_video["path"], ws.source_path(VID))
    ws.upsert_video(
        VideoMeta(id=VID, source=str(synthetic_video["path"]), title="Synthetic Test Video",
                  duration=60.0, width=640, height=360, fps=24.0,
                  created_at="2026-06-11T00:00:00")
    )
    ids = ws.add_segments([
        _seg(5.0, 10.0, "the zebra escaped from the city zoo this morning"),
        _seg(20.0, 26.0, "quantum computers solve certain problems much faster now"),
    ])
    cuts = [
        Cut(5.0, 10.0, "zebra escape",
            Evidence(segment_ids=[ids[0]], frame_ts=[6.0, 9.0], note="strong opening line")),
        Cut(20.0, 26.0, "quantum claim",
            Evidence(segment_ids=[ids[1]], frame_ts=[22.0], note="")),
    ]
    return ws, cuts


def _probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
         str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def _duration(path: Path) -> float:
    return float(_probe(path)["format"]["duration"])


def test_render_edl_landscape_and_receipts(tmp_path, synthetic_video):
    ws, cuts = _setup(tmp_path, synthetic_video)
    edl = EDL(video_id=VID, cuts=cuts, target="landscape", captions=True)
    outputs = render_edl(ws, edl)
    assert len(outputs) == 2 and all(p.exists() for p in outputs)
    assert abs(_duration(outputs[0]) - 5.0) <= 0.6
    assert abs(_duration(outputs[1]) - 6.0) <= 0.6
    renders = ws.renders_dir(VID)
    assert (renders / "clip_01.ass").exists() and (renders / "clip_02.ass").exists()

    moments = [Moment(19.5, 26.5, "fast science explainer", score=0.9)]
    receipts = write_receipts(ws, edl, outputs, moments=moments)
    assert receipts == renders / "receipts.md"
    text = receipts.read_text()
    assert "Synthetic Test Video" in text
    assert "Cut 1 — zebra escape" in text and "Cut 2 — quantum claim" in text
    assert "00:05–00:10" in text and "00:20–00:26" in text
    assert "strong opening line" in text  # evidence.note
    assert "fast science explainer" in text  # matching moment reason for the bare cut
    assert "the zebra escaped" in text  # transcript excerpt
    thumbs = sorted((renders / "thumbs").glob("*.jpg"))
    assert len(thumbs) == 3  # one per evidence frame_ts
    assert text.count("](thumbs/") == 3
    assert f"]({outputs[0].name})" in text
    ws.close()


def test_render_edl_vertical(tmp_path, synthetic_video):
    ws, cuts = _setup(tmp_path, synthetic_video)
    edl = EDL(video_id=VID, cuts=cuts[:1], target="vertical", captions=True)
    outputs = render_edl(ws, edl, basename="vert")
    assert len(outputs) == 1
    stream = next(
        s for s in _probe(outputs[0])["streams"] if s["codec_type"] == "video"
    )
    assert (stream["width"], stream["height"]) == (1080, 1920)
    assert abs(_duration(outputs[0]) - 5.0) <= 0.6
    ws.close()


def test_render_reel(tmp_path, synthetic_video):
    ws, cuts = _setup(tmp_path, synthetic_video)
    edl = EDL(video_id=VID, cuts=cuts, target="landscape", captions=True)
    reel = render_reel(ws, edl)
    assert reel.exists() and reel.name == "reel.mp4"
    assert abs(_duration(reel) - 11.0) <= 1.2
    ws.close()
