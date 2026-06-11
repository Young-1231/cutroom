"""receipts.md: a human-auditable report of why each cut was made.

Per cut: label, time range, the agent's reason, the cited transcript excerpt,
thumbnails of the frames it actually viewed, and a link to the rendered file.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

from cutroom.db import Workspace
from cutroom.ffmpeg_util import run_ffmpeg
from cutroom.types import EDL, Cut, Moment


def write_receipts(
    ws: Workspace, edl: EDL, outputs: list[Path], moments: list[Moment] | None = None
) -> Path:
    meta = ws.get_video(edl.video_id)
    title = meta.title if meta and meta.title else edl.video_id
    out_dir = ws.renders_dir(edl.video_id)
    thumbs_dir = out_dir / "thumbs"
    thumbs_dir.mkdir(exist_ok=True)
    src = ws.source_path(edl.video_id)
    total = sum(c.t1 - c.t0 for c in edl.cuts)

    lines = [
        f"# Receipts — {title}",
        "",
        f"- Date: {date.today().isoformat()}",
        f"- Edit runtime: {_mmss(total)} across {len(edl.cuts)} cut(s)",
        "",
    ]
    for i, cut in enumerate(edl.cuts, start=1):
        heading = f"## Cut {i} — {cut.label}" if cut.label else f"## Cut {i}"
        lines += [heading, "", f"**Time:** {_mmss(cut.t0)}–{_mmss(cut.t1)}", ""]
        why = cut.evidence.note or _moment_reason(cut, moments)
        if why:
            lines += [f"**Why:** {why}", ""]
        for seg in ws.get_segments_by_ids(cut.evidence.segment_ids):
            lines.append(f"> [{_mmss(seg.t0)}] {seg.text}")
        if cut.evidence.segment_ids:
            lines.append("")
        for ts in cut.evidence.frame_ts:
            thumb = _extract_thumb(src, ts, thumbs_dir, i)
            if thumb is not None:
                lines.append(f"![frame {ts:.1f}s](thumbs/{thumb.name})")
        if cut.evidence.frame_ts:
            lines.append("")
        if i <= len(outputs):
            lines += [f"**Output:** [{outputs[i - 1].name}]({_rel(outputs[i - 1], out_dir)})", ""]
    path = out_dir / "receipts.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _moment_reason(cut: Cut, moments: list[Moment] | None) -> str:
    for m in moments or []:
        if m.t0 < cut.t1 and m.t1 > cut.t0:
            return m.reason
    return ""


def _extract_thumb(src: Path, ts: float, thumbs_dir: Path, cut_no: int) -> Path | None:
    """320px-wide JPEG at ts; None when neither a saved frame nor the source is usable.

    view_frames already wrote a 768px JPEG for every viewed timestamp, so reuse it
    when present and only fall back to re-extracting from the source (e.g. the
    `cutroom render` path, which never ran the agent)."""
    out = thumbs_dir / f"cut{cut_no:02d}_{ts:08.2f}s.jpg"
    # view_frames saves frames at media/<id>/frames/f<ts>.jpg, beside source.mp4.
    cached = src.parent / "frames" / f"f{ts:09.3f}.jpg"
    if cached.exists():
        shutil.copyfile(cached, out)
        return out
    if not src.exists():
        return None
    try:
        run_ffmpeg([
            "-ss", f"{ts:.3f}", "-i", str(src),
            "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "3", str(out),
        ])
    except (RuntimeError, OSError):
        return None
    return out if out.exists() else None


def _mmss(t: float) -> str:
    s = max(0, int(round(t)))
    return f"{s // 60:02d}:{s % 60:02d}"


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
