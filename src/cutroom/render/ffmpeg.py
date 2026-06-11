"""EDL -> mp4 via ffmpeg: per-cut clips, 9:16 crop, caption burn-in, reel concat."""

from __future__ import annotations

from pathlib import Path

from cutroom.db import Workspace
from cutroom.ffmpeg_util import resolve_ffmpeg, run_ffmpeg
from cutroom.render.captions import ass_for_cut, write_ass
from cutroom.types import EDL

# Re-exported for backward compatibility; the canonical home is cutroom.ffmpeg_util.
__all__ = ["render_edl", "render_reel", "resolve_ffmpeg"]

# -ss/-to before -i is frame-accurate here because every cut is re-encoded.
_ENCODE = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-c:a", "aac", "-b:a", "128k",
    "-movflags", "+faststart",
]
_VERTICAL_VF = "crop=ih*9/16:ih,scale=1080:1920"
_LANDSCAPE_VF = "scale=-2:min(1080\\,ih)"  # keep aspect, cap height at 1080


def render_edl(ws: Workspace, edl: EDL, basename: str = "clip") -> list[Path]:
    """Render one mp4 per cut into ws.renders_dir(edl.video_id)."""
    src = ws.source_path(edl.video_id)
    if not src.exists():
        raise FileNotFoundError(f"source video missing: {src}")
    out_dir = ws.renders_dir(edl.video_id)
    vertical = edl.target == "vertical"
    outputs: list[Path] = []
    can_burn = resolve_ffmpeg()[1]
    for i, cut in enumerate(edl.cuts, start=1):
        filters = [_VERTICAL_VF if vertical else _LANDSCAPE_VF]
        if edl.captions:
            segments = ws.get_segments(edl.video_id, cut.t0, cut.t1)
            ass_path = write_ass(ws, edl.video_id, i, ass_for_cut(segments, cut, vertical))
            if can_burn:
                filters.append(f"subtitles=filename={_filter_escape(ass_path)}")
        out = out_dir / f"{basename}_{i:02d}.mp4"
        run_ffmpeg([
            "-ss", f"{cut.t0:.3f}", "-to", f"{cut.t1:.3f}", "-i", str(src),
            "-vf", ",".join(filters), *_ENCODE, str(out),
        ])
        outputs.append(out)
    return outputs


def render_reel(ws: Workspace, edl: EDL, name: str = "reel") -> Path:
    """Render every cut, then concat (demuxer, stream copy) into one mp4."""
    parts = render_edl(ws, edl, basename=f"{name}_part")
    out_dir = ws.renders_dir(edl.video_id)
    concat_list = out_dir / f"{name}_concat.txt"
    concat_list.write_text(
        "".join(f"file '{_concat_escape(p)}'\n" for p in parts), encoding="utf-8"
    )
    out = out_dir / f"{name}.mp4"
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", "-movflags", "+faststart", str(out),
    ])
    return out


def _filter_escape(path: Path) -> str:
    """Escape a path for use inside a filtergraph option value.

    Beyond the option/value separators (`\\ : ' ,`), filtergraph link delimiters
    `[ ]` and chain separator `;` must be escaped too, or a workspace under e.g.
    `~/Footage [RAW]/` makes the `subtitles=` filter unparseable."""
    s = str(path)
    for ch in ("\\", ":", "'", ",", "[", "]", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


def _concat_escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")
