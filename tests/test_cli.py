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


def test_log_bad_source_is_friendly(tmp_path, monkeypatch):
    """A missing local file must produce a one-line error, not a traceback."""
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))
    res = runner.invoke(app, ["log", str(tmp_path / "does-not-exist.mp4")])
    assert res.exit_code == 1
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "Error" in res.output or "error" in res.output


def test_render_missing_edl_is_friendly(tmp_path, monkeypatch, seeded_ws):
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    res = runner.invoke(app, ["render", "testvid000001"])
    assert res.exit_code == 1
    assert "no saved EDL" in res.output


def test_render_corrupt_edl_is_friendly(tmp_path, monkeypatch, seeded_ws):
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    (seeded_ws.renders_dir("testvid000001") / "edl.json").write_text("{not json")
    res = runner.invoke(app, ["render", "testvid000001"])
    assert res.exit_code == 1
    assert "not a valid EDL" in res.output


def test_highlights_plan_mode_saves_without_rendering(monkeypatch, seeded_ws):
    """--plan prints the plan and writes edl.json but renders nothing."""
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    from cutroom.agent import runner
    from cutroom.types import EDL, Cut, Evidence, Moment

    vid = "testvid000001"
    fake = runner.EditorResult(
        final_text="found one strong moment",
        edl=EDL(video_id=vid,
                cuts=[Cut(16.0, 28.0, "deep dive", Evidence([2], [20.0]))],
                target="landscape", captions=True),
        moments=[Moment(16.0, 28.0, "vivid depth explanation", Evidence([2], [20.0]))],
        chars_used=1234, num_turns=5, ok=True, error=None,
    )
    monkeypatch.setattr(runner, "run_editor_sync", lambda *a, **k: fake)

    out = CliRunner().invoke(app, ["highlights", vid, "--plan", "-n", "1"])
    assert out.exit_code == 0, out.output
    assert "Edit plan" in out.output
    assert "vivid depth explanation" in out.output  # the cited reason
    assert "plan saved" in out.output and "cutroom render" in out.output
    assert (seeded_ws.renders_dir(vid) / "edl.json").exists()
    assert not list(seeded_ws.renders_dir(vid).glob("clip_*.mp4"))  # nothing rendered
