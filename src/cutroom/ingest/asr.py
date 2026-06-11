"""Word-level transcription with faster-whisper (CPU int8 — GPU-free by project rule)."""

from __future__ import annotations

import os
from collections.abc import Callable

from cutroom.db import Workspace
from cutroom.types import Segment, Word

DEFAULT_MODEL = "small"


def transcribe(
    ws: Workspace,
    video_id: str,
    model_size: str | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> list[Segment]:
    """Transcribe audio.wav into word-timestamped segments and store them.

    Model precedence: arg > $CUTROOM_WHISPER_MODEL > "small".
    `on_progress(seconds_done)` fires per decoded segment — hour-long footage takes
    many minutes on CPU and a silent spinner reads as a hang.
    """
    size = model_size or os.environ.get("CUTROOM_WHISPER_MODEL") or DEFAULT_MODEL
    # Imported here: ctranslate2 is a heavy import and most callers never transcribe.
    from faster_whisper import WhisperModel

    model = WhisperModel(size, device="cpu", compute_type="int8")
    raw, _info = model.transcribe(
        str(ws.audio_path(video_id)), word_timestamps=True, vad_filter=True
    )

    ws.delete_segments(video_id)  # idempotent re-run: drop any prior transcription
    segments = []
    for s in raw:
        if on_progress is not None:
            on_progress(float(s.end))
        words = [Word(text=w.word.strip(), t0=float(w.start), t1=float(w.end))
                 for w in (s.words or [])]
        segments.append(
            Segment(id=None, video_id=video_id, t0=float(s.start), t1=float(s.end),
                    text=s.text.strip(), words=words)
        )
    ids = ws.add_segments(segments)
    for seg, seg_id in zip(segments, ids, strict=True):
        seg.id = seg_id
    return segments
