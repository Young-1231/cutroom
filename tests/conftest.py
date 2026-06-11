"""Shared fixtures.

Two tiers so most tests stay fast and offline:
- `seeded_ws`: a Workspace populated with pure data (no media) — for index/agent/render logic.
- `synthetic_video`: a real 60s mp4 built with ffmpeg (4 color blocks, speech via macOS `say`
  when available, sine tones otherwise) — for ingest and render tests (marked slow).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cutroom.db import Workspace
from cutroom.types import AudioEvent, Scene, Segment, Shot, VideoMeta, Word

HAS_FFMPEG = shutil.which("ffmpeg") is not None
HAS_SAY = shutil.which("say") is not None

# One spoken sentence per 15s color block; each carries a unique, ASR-friendly keyword.
BLOCKS = [
    ("red", "The zebra escaped from the city zoo this morning and ran across the bridge."),
    ("green", "Quantum computers solve certain problems much faster than classical machines."),
    ("blue", "The volcano erupted twice last year, covering the valley in gray ash."),
    ("yellow", "Farmers finished the harvest early because the autumn weather stayed dry."),
]
BLOCK_SECONDS = 15.0


@pytest.fixture
def seeded_ws(tmp_path: Path) -> Workspace:
    """Workspace with a fake 120s talk: 6 shots, 8 segments, 3 scenes, silences."""
    ws = Workspace(home=tmp_path / "cutroom-home")
    vid = "testvid000001"
    ws.upsert_video(
        VideoMeta(id=vid, source="/fake/talk.mp4", title="Fake Talk", duration=120.0,
                  width=1280, height=720, fps=30.0, created_at="2026-06-11T00:00:00")
    )
    ws.add_shots([Shot(None, vid, t0, t1) for t0, t1 in
                  [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100), (100, 120)]])
    texts = [
        (1.0, 13.0, "welcome everyone to this talk about deep sea exploration"),
        (16.0, 28.0, "submarines can reach depths of nearly eleven thousand meters"),
        (31.0, 43.0, "the pressure down there would crush an ordinary vehicle instantly"),
        (46.0, 58.0, "we discovered three new species of fish on the last expedition"),
        (61.0, 73.0, "one of them glows in the dark using bioluminescent organs"),
        (76.0, 88.0, "funding for ocean science remains far below space research budgets"),
        (91.0, 103.0, "our next mission will map the mariana trench in full detail"),
        (106.0, 118.0, "thank you all for listening and please support ocean science"),
    ]
    segs = []
    for t0, t1, text in texts:
        words = text.split()
        step = (t1 - t0) / len(words)
        segs.append(Segment(None, vid, t0, t1, text,
                            words=[Word(w, t0 + i * step, t0 + (i + 1) * step)
                                   for i, w in enumerate(words)]))
    ws.add_segments(segs)
    ws.add_audio_events([
        AudioEvent(None, vid, "silence", 13.0, 16.0, -60.0),
        AudioEvent(None, vid, "silence", 43.0, 46.0, -60.0),
        AudioEvent(None, vid, "silence", 88.0, 91.0, -60.0),
        AudioEvent(None, vid, "loud", 61.0, 63.0, -8.0),
    ])
    ws.replace_scenes(vid, [
        Scene(None, vid, 0.0, 43.0, "Going deep", "Intro and submarine capabilities."),
        Scene(None, vid, 43.0, 88.0, "New species", "Discoveries including a glowing fish."),
        Scene(None, vid, 88.0, 120.0, "The next mission", "Funding gap and Mariana plans."),
    ])
    yield ws
    ws.close()


@pytest.fixture(scope="session")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build a real 60s mp4: 4 distinct color blocks; spoken audio when `say` exists.

    Returns {"path": Path, "blocks": BLOCKS, "block_seconds": float, "has_speech": bool}.
    """
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    root = tmp_path_factory.mktemp("synthetic")
    parts = []
    for i, (color, sentence) in enumerate(BLOCKS):
        apath = root / f"a{i}.wav"
        if HAS_SAY:
            aiff = root / f"a{i}.aiff"
            subprocess.run(["say", "-o", str(aiff), sentence], check=True, capture_output=True)
            _run_ffmpeg(["-i", str(aiff), "-ar", "16000", "-ac", "1",
                         "-af", f"apad=whole_dur={BLOCK_SECONDS}",
                         "-t", str(BLOCK_SECONDS), str(apath)])
        else:
            freq = 220 * (i + 1)
            _run_ffmpeg(["-f", "lavfi", "-i",
                         f"sine=frequency={freq}:duration={BLOCK_SECONDS}",
                         "-ar", "16000", "-ac", "1", str(apath)])
        vpath = root / f"v{i}.mp4"
        _run_ffmpeg(["-f", "lavfi", "-i",
                     f"color=c={color}:size=640x360:duration={BLOCK_SECONDS}:rate=24",
                     "-i", str(apath), "-c:v", "libx264", "-preset", "ultrafast",
                     "-c:a", "aac", "-shortest", str(vpath)])
        parts.append(vpath)
    concat_list = root / "list.txt"
    concat_list.write_text("".join(f"file '{p}'\n" for p in parts))
    out = root / "synthetic.mp4"
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(out)])
    return {"path": out, "blocks": BLOCKS, "block_seconds": BLOCK_SECONDS,
            "has_speech": HAS_SAY}


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                   check=True, capture_output=True)
