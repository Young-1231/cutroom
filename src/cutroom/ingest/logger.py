"""Orchestrates the full logging pass: fetch -> shots -> ASR -> audio -> scenes."""

from __future__ import annotations

from collections.abc import Callable

from cutroom.db import Workspace
from cutroom.ingest.asr import transcribe
from cutroom.ingest.audio import detect_audio_events
from cutroom.ingest.fetch import fetch
from cutroom.ingest.shots import detect_shots
from cutroom.types import VideoMeta


def log_footage(
    source: str,
    ws: Workspace,
    summarize: bool = False,
    model_size: str | None = None,
    on_step: Callable[[str], None] | None = None,
) -> VideoMeta:
    """Run the whole ingest pipeline for one source and return its metadata.

    `on_step(name)` fires before each stage (CLI progress). Scene building lives
    in cutroom.index; if that module isn't available yet we skip it silently.
    `summarize` is reserved for the index module's LLM summarizer hook.
    """

    def step(name: str) -> None:
        if on_step is not None:
            on_step(name)

    step("fetch")
    meta = fetch(source, ws)
    step("shots")
    detect_shots(ws, meta.id)
    step("transcribe")

    def asr_progress(done: float) -> None:
        if meta.duration > 0:
            step(f"transcribe {done / meta.duration:.0%} ({done:.0f}s/{meta.duration:.0f}s)")

    transcribe(ws, meta.id, model_size=model_size, on_progress=asr_progress)
    step("audio")
    detect_audio_events(ws, meta.id)

    step("scenes")
    try:
        from cutroom.index.map import build_and_store_scenes
    except ImportError:
        return meta  # index module not built yet — shots/segments are still usable
    build_and_store_scenes(ws, meta.id, summarizer=None)
    return meta
