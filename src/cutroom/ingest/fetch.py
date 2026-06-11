"""Fetch a source (URL via yt-dlp, or local file) into the workspace.

Normalizes to media/<video_id>/source.mp4 (h264/aac) plus audio.wav
(16 kHz mono, what faster-whisper and the audio analyzers consume).
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cutroom.db import Workspace, video_id_for
from cutroom.types import VideoMeta


def fetch(source: str, ws: Workspace, max_height: int = 1080) -> VideoMeta:
    """Download/normalize `source`, extract 16k mono wav, upsert and return metadata.

    Idempotent: existing source.mp4 / audio.wav are kept as-is.
    """
    if source.startswith(("http://", "https://")):
        video_id = video_id_for(source)
        title = _download(source, ws, video_id, max_height)
    else:
        src = Path(source).expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"no such file: {source}")
        source = str(src)
        video_id = video_id_for(source)
        title = src.stem
        _normalize_local(src, ws.source_path(video_id))

    mp4 = ws.source_path(video_id)
    fmt, streams = _probe(mp4)
    duration = float(fmt.get("duration", 0.0))
    vstream = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    _extract_audio(mp4, ws.audio_path(video_id), duration, has_audio)

    meta = VideoMeta(
        id=video_id,
        source=source,
        title=title,
        duration=duration,
        width=int(vstream.get("width", 0)),
        height=int(vstream.get("height", 0)),
        fps=_parse_fps(vstream),
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    ws.upsert_video(meta)
    return meta


def _download(url: str, ws: Workspace, video_id: str, max_height: int) -> str:
    out = ws.source_path(video_id)
    info_path = out.with_suffix(".info.json")
    if not out.exists():
        fmt = (
            f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]"
            f"/bv*[height<={max_height}]+ba"
            f"/b[height<={max_height}]/b"
        )
        subprocess.run(
            [
                "yt-dlp", "--no-playlist", "-f", fmt,
                "--merge-output-format", "mp4", "--remux-video", "mp4",
                "--write-info-json", "-o", str(out.with_suffix(".%(ext)s")), url,
            ],
            check=True,
        )
    if info_path.exists():
        try:
            return str(json.loads(info_path.read_text()).get("title") or url)
        except (json.JSONDecodeError, OSError):
            pass
    existing = ws.get_video(video_id)
    return existing.title if existing else url


def _normalize_local(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    fmt, streams = _probe(src)
    vcodec = next((s["codec_name"] for s in streams if s.get("codec_type") == "video"), "")
    acodecs = {s["codec_name"] for s in streams if s.get("codec_type") == "audio"}
    is_mp4 = "mp4" in fmt.get("format_name", "")
    if is_mp4 and vcodec == "h264" and acodecs <= {"aac"}:
        codec_args = ["-c", "copy"]
    else:
        codec_args = ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac"]
    _ffmpeg(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", *codec_args,
             "-movflags", "+faststart", str(dst)])


def _extract_audio(mp4: Path, wav: Path, duration: float, has_audio: bool) -> None:
    if wav.exists():
        return
    if has_audio:
        _ffmpeg(["-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000",
                 "-c:a", "pcm_s16le", str(wav)])
    else:
        # Keep downstream stages uniform: silent track of matching length.
        _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                 "-t", f"{duration:.3f}", str(wav)])


def _probe(path: Path) -> tuple[dict, list[dict]]:
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(proc.stdout)
    return data.get("format", {}), data.get("streams", [])


def _parse_fps(vstream: dict) -> float:
    for key in ("avg_frame_rate", "r_frame_rate"):
        num, _, den = (vstream.get(key) or "").partition("/")
        try:
            if float(den) > 0:
                return float(num) / float(den)
        except ValueError:
            continue
    return 0.0


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-loglevel", "error", *args],
        check=True, capture_output=True,
    )
