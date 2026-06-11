"""CLI wiring tests (offline; agent commands are exercised in test_e2e)."""

import shutil

import pytest
from typer.testing import CliRunner

from cutroom.cli import app

HAS_FFMPEG = shutil.which("ffmpeg") is not None
runner = CliRunner()


def test_help_lists_all_commands():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    for cmd in ["log", "list", "map", "ask", "highlights", "chapters", "cut"]:
        assert cmd in res.output


def test_unknown_video_is_friendly(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))
    res = runner.invoke(app, ["map", "nope"])
    assert res.exit_code == 1
    assert "no unique video" in res.output


def test_list_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))
    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "nothing logged yet" in res.output


@pytest.mark.slow
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_log_then_map_and_list(tmp_path, monkeypatch, synthetic_video, whisper_tiny):
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))
    monkeypatch.setenv("CUTROOM_WHISPER_MODEL", "tiny")
    res = runner.invoke(app, ["log", str(synthetic_video["path"])])
    assert res.exit_code == 0, res.output
    assert "logged" in res.output
    assert "🎬" in res.output

    res = runner.invoke(app, ["list"])
    assert res.exit_code == 0
    assert "01:00" in res.output

    res = runner.invoke(app, ["map", "synthetic"])
    assert res.exit_code == 0
    assert "scenes" in res.output


@pytest.mark.slow
@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_render_from_saved_edl(tmp_path, monkeypatch, synthetic_video, seeded_ws):
    import json
    import shutil as sh

    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    vid = "testvid000001"
    sh.copy(synthetic_video["path"], seeded_ws.source_path(vid))
    edl = {
        "video_id": vid,
        "cuts": [{"t0": 5.0, "t1": 10.0, "label": "demo", "evidence": {}}],
        "target": "landscape",
        "captions": False,
    }
    (seeded_ws.renders_dir(vid) / "edl.json").write_text(json.dumps(edl))

    res = runner.invoke(app, ["render", vid, "--basename", "re"])
    assert res.exit_code == 0, res.output
    assert (seeded_ws.renders_dir(vid) / "re_01.mp4").exists()

    res = runner.invoke(app, ["render", vid, "--target", "bogus"])
    assert res.exit_code == 1

    res = runner.invoke(app, ["render", "missing-video"])
    assert res.exit_code == 1
