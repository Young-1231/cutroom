"""Word-timestamp ASS captions for rendered clips.

One ASS file per cut, with times re-based to the clip (t - cut.t0) so they line
up with the re-encoded output that starts at 0.
"""

from __future__ import annotations

from pathlib import Path

from cutroom.db import Workspace
from cutroom.types import Cut, Segment

MAX_LINE_WORDS = 4
MAX_LINE_SECONDS = 1.6
MAX_WORD_GAP = 0.6  # silence longer than this starts a fresh line

_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: {x}
PlayResY: {y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, \
Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, \
Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,\
1,4,0,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_for_cut(segments: list[Segment], cut: Cut, vertical: bool) -> str:
    """Full ASS file content for one rendered clip of [cut.t0, cut.t1]."""
    clip_len = cut.t1 - cut.t0
    words = []
    for seg in segments:
        for w in seg.words:
            if w.t1 <= cut.t0 or w.t0 >= cut.t1:
                continue
            t0 = max(0.0, w.t0 - cut.t0)
            t1 = min(clip_len, w.t1 - cut.t0)
            if t1 > t0:
                words.append((_clean(w.text), t0, t1))
    words.sort(key=lambda w: w[1])

    lines: list[list[tuple[str, float, float]]] = []
    for word in words:
        if lines and _fits(lines[-1], word):
            lines[-1].append(word)
        else:
            lines.append([word])

    if vertical:
        # Bottom-anchored with a large margin lands the text mid-lower frame.
        header = _HEADER.format(x=1080, y=1920, size=64, margin_lr=60, margin_v=640)
    else:
        header = _HEADER.format(x=1920, y=1080, size=72, margin_lr=120, margin_v=120)
    events = []
    for line in lines:
        start, end = line[0][1], line[-1][2]
        text = " ".join(w[0] for w in line)
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}"
        )
    return header + "\n".join(events) + ("\n" if events else "")


def write_ass(ws: Workspace, video_id: str, cut_index: int, content: str) -> Path:
    path = ws.renders_dir(video_id) / f"clip_{cut_index:02d}.ass"
    path.write_text(content, encoding="utf-8")
    return path


def _fits(line: list[tuple[str, float, float]], word: tuple[str, float, float]) -> bool:
    if len(line) >= MAX_LINE_WORDS:
        return False
    if word[2] - line[0][1] > MAX_LINE_SECONDS:
        return False
    return word[1] - line[-1][2] <= MAX_WORD_GAP


def _clean(text: str) -> str:
    # ASS treats {...} as override blocks and newlines as event separators.
    return text.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def _ass_time(t: float) -> str:
    """h:mm:ss.cc, the ASS timestamp format."""
    cs = max(0, round(t * 100))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"
