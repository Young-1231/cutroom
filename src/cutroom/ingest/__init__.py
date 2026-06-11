"""Ingest: fetch/normalize media, detect shots, transcribe, flag audio events.

`log_footage` is the one-call pipeline used by the CLI `log` verb.
"""

from cutroom.ingest.asr import transcribe
from cutroom.ingest.audio import detect_audio_events
from cutroom.ingest.fetch import fetch
from cutroom.ingest.logger import log_footage
from cutroom.ingest.shots import detect_shots

__all__ = ["detect_audio_events", "detect_shots", "fetch", "log_footage", "transcribe"]
