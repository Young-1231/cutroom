"""ffmpeg binary resolution + the shared runner (offline; no real ffmpeg needed)."""

import shutil
import subprocess

import pytest

from cutroom import ffmpeg_util

# `false` exists on every POSIX system, ignores its args, and exits nonzero — a stand-in
# for "ffmpeg ran but failed" without needing a real ffmpeg.
FALSE = shutil.which("false") or "/usr/bin/false"


@pytest.fixture(autouse=True)
def _clear_cache():
    ffmpeg_util.reset_ffmpeg_cache()
    yield
    ffmpeg_util.reset_ffmpeg_cache()


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("CUTROOM_FFMPEG", "/opt/custom/ffmpeg")
    monkeypatch.setattr(ffmpeg_util, "_supports_subtitles", lambda b: b.endswith("ffmpeg"))
    binary, can_burn = ffmpeg_util.resolve_ffmpeg()
    assert binary == "/opt/custom/ffmpeg"
    assert can_burn is True


def test_falls_back_to_static_ffmpeg_when_system_lacks_libass(monkeypatch):
    monkeypatch.delenv("CUTROOM_FFMPEG", raising=False)
    # System ffmpeg present but without the subtitles filter → use static-ffmpeg.
    monkeypatch.setattr(ffmpeg_util, "_supports_subtitles",
                        lambda b: b == "/bundled/ffmpeg")
    import static_ffmpeg.run as sfr

    monkeypatch.setattr(sfr, "get_or_fetch_platform_executables_else_raise",
                        lambda: ("/bundled/ffmpeg", "/bundled/ffprobe"))
    binary, can_burn = ffmpeg_util.resolve_ffmpeg()
    assert binary == "/bundled/ffmpeg" and can_burn is True


def test_cache_reset_picks_up_new_env(monkeypatch):
    monkeypatch.setattr(ffmpeg_util, "_supports_subtitles", lambda b: False)
    monkeypatch.setenv("CUTROOM_FFMPEG", "/first/ffmpeg")
    assert ffmpeg_util.resolve_ffmpeg()[0] == "/first/ffmpeg"
    monkeypatch.setenv("CUTROOM_FFMPEG", "/second/ffmpeg")
    assert ffmpeg_util.resolve_ffmpeg()[0] == "/first/ffmpeg"  # still cached
    ffmpeg_util.reset_ffmpeg_cache()
    assert ffmpeg_util.resolve_ffmpeg()[0] == "/second/ffmpeg"


def test_run_ffmpeg_raises_on_failure(monkeypatch):
    monkeypatch.setattr(ffmpeg_util, "resolve_ffmpeg", lambda: (FALSE, False))
    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        ffmpeg_util.run_ffmpeg(["-i", "nope.mp4", "out.mp4"])


def test_run_ffmpeg_analysis_raises_on_failure(monkeypatch):
    monkeypatch.setattr(ffmpeg_util, "ffmpeg_binary", lambda: FALSE)
    with pytest.raises(RuntimeError, match="analysis failed"):
        ffmpeg_util.run_ffmpeg_analysis(["-i", "nope.wav", "-af", "silencedetect"])


def test_run_ffmpeg_check_false_returns_proc(monkeypatch):
    monkeypatch.setattr(ffmpeg_util, "resolve_ffmpeg", lambda: (FALSE, False))
    proc = ffmpeg_util.run_ffmpeg(["-version"], check=False)
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode != 0
