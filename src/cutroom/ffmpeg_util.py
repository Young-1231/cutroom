"""Single source of truth for invoking ffmpeg.

Every ffmpeg call in cutroom goes through here so binary resolution, return-code
checking, and locale-safe stderr decoding happen in exactly one place.

The bundled `static-ffmpeg` exists because slim system builds (current Homebrew
bottles) ship without libass, so `subtitles` burn-in is unavailable. `resolve_ffmpeg`
picks the best binary and reports whether it can burn captions.
"""

from __future__ import annotations

import functools
import os
import subprocess


@functools.lru_cache(maxsize=1)
def resolve_ffmpeg() -> tuple[str, bool]:
    """(binary, can_burn_captions). Order: $CUTROOM_FFMPEG, system ffmpeg, static-ffmpeg."""
    env = os.environ.get("CUTROOM_FFMPEG")
    if env:
        return env, _supports_subtitles(env)
    if _supports_subtitles("ffmpeg"):
        return "ffmpeg", True
    try:
        from static_ffmpeg.run import get_or_fetch_platform_executables_else_raise

        binary, _ = get_or_fetch_platform_executables_else_raise()
        return str(binary), _supports_subtitles(str(binary))
    except Exception:
        # No caption-capable binary; fall back to system ffmpeg with sidecar-only
        # captions. Note: a transient failure here is cached for the process lifetime
        # (see reset_ffmpeg_cache for tests) — acceptable since the alternative is
        # re-probing on every render.
        return "ffmpeg", False


def _supports_subtitles(binary: str) -> bool:
    """Whether this ffmpeg build has the libass-backed `subtitles` filter."""
    try:
        proc = subprocess.run(
            [binary, "-hide_banner", "-filters"], capture_output=True, text=True, check=False
        )
    except OSError:
        return False
    return any(
        len(parts := line.split()) >= 2 and parts[1] == "subtitles"
        for line in proc.stdout.splitlines()
    )


def reset_ffmpeg_cache() -> None:
    """Clear the resolve_ffmpeg cache (after changing $CUTROOM_FFMPEG, mainly in tests)."""
    resolve_ffmpeg.cache_clear()


def ffmpeg_binary() -> str:
    return resolve_ffmpeg()[0]


def run_ffmpeg(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run `ffmpeg <args>` with the resolved binary. Raises RuntimeError on failure
    when check=True. stderr is decoded latin-1 so non-UTF8 media metadata never crashes
    the parse (the values cutroom scrapes are ASCII)."""
    proc = subprocess.run(
        [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, encoding="latin-1", errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode}): {' '.join(args)}\n"
            f"{(proc.stderr or '').strip()[-800:]}"
        )
    return proc


def run_ffmpeg_analysis(args: list[str]) -> str:
    """Run an analysis pass (output to null) and return its stderr log.

    For silencedetect/ebur128/showinfo, whose results are printed to stderr. Uses
    -nostats so only the filters' own lines appear; raises if ffmpeg itself fails so a
    missing/corrupt input can never be silently misread as 'no events detected'."""
    proc = subprocess.run(
        [ffmpeg_binary(), "-nostdin", "-hide_banner", "-nostats", *args, "-f", "null", "-"],
        capture_output=True, encoding="latin-1", errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-500:]
        raise RuntimeError(f"ffmpeg analysis failed (exit {proc.returncode}):\n{tail}")
    return proc.stderr
