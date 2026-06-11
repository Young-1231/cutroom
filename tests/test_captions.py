"""Pure-logic tests for ASS caption generation."""

import re

from cutroom.render.captions import ass_for_cut, write_ass
from cutroom.types import Cut, Segment, Word

TIME_RE = re.compile(r"^\d:\d{2}:\d{2}\.\d{2}$")


def _segment(words: list[tuple[str, float, float]]) -> Segment:
    return Segment(
        id=1, video_id="v", t0=words[0][1], t1=words[-1][2],
        text=" ".join(w[0] for w in words),
        words=[Word(text=t, t0=a, t1=b) for t, a, b in words],
    )


def _dialogues(content: str) -> list[tuple[float, float, str]]:
    out = []
    for line in content.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(":", 1)[1].strip().split(",", 9)
        out.append((_seconds(fields[1]), _seconds(fields[2]), fields[9]))
    return out


def _seconds(stamp: str) -> float:
    assert TIME_RE.match(stamp), stamp
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def test_lines_capped_at_four_words():
    # Eight tight 0.3s words: 4 fit in 1.2s, so the word cap is what splits.
    words = [(f"w{i}", 10.0 + 0.3 * i, 10.0 + 0.3 * (i + 1)) for i in range(8)]
    content = ass_for_cut([_segment(words)], Cut(10.0, 14.0), vertical=False)
    dialogues = _dialogues(content)
    assert len(dialogues) == 2
    assert all(len(text.split()) == 4 for _, _, text in dialogues)


def test_lines_capped_at_max_duration():
    # 0.7s words: a third word would stretch the line to 2.1s > 1.6s.
    words = [(f"w{i}", 10.0 + 0.7 * i, 10.0 + 0.7 * (i + 1)) for i in range(6)]
    content = ass_for_cut([_segment(words)], Cut(10.0, 15.0), vertical=False)
    for start, end, text in _dialogues(content):
        assert end - start <= 1.6 + 0.01
        assert len(text.split()) <= 4


def test_gap_starts_new_line():
    words = [("hello", 10.0, 10.4), ("again", 12.0, 12.4)]
    content = ass_for_cut([_segment(words)], Cut(10.0, 14.0), vertical=False)
    dialogues = _dialogues(content)
    assert len(dialogues) == 2
    assert dialogues[0][1] <= 0.4 + 0.01  # caption clears before the silence


def test_clip_local_rebasing_and_word_filtering():
    words = [("early", 5.0, 6.0), ("inside", 11.0, 11.8), ("late", 20.0, 21.0)]
    content = ass_for_cut([_segment(words)], Cut(10.0, 14.0), vertical=False)
    dialogues = _dialogues(content)
    assert len(dialogues) == 1
    start, end, text = dialogues[0]
    assert text == "inside"
    assert abs(start - 1.0) < 0.011  # 11.0 - cut.t0
    assert abs(end - 1.8) < 0.011


def test_playres_landscape_vs_vertical():
    words = [("hi", 10.0, 10.5)]
    landscape = ass_for_cut([_segment(words)], Cut(10.0, 12.0), vertical=False)
    assert "PlayResX: 1920" in landscape and "PlayResY: 1080" in landscape
    vertical = ass_for_cut([_segment(words)], Cut(10.0, 12.0), vertical=True)
    assert "PlayResX: 1080" in vertical and "PlayResY: 1920" in vertical


def test_timestamp_format_h_mm_ss_cc():
    words = [("one", 3661.0, 3661.5), ("two", 3661.5, 3662.0)]
    content = ass_for_cut([_segment(words)], Cut(3661.0, 3663.0), vertical=False)
    stamps = re.findall(r"Dialogue: \d,([^,]+),([^,]+),", content)
    assert stamps
    for start, end in stamps:
        assert TIME_RE.match(start), start
        assert TIME_RE.match(end), end


def test_write_ass_path(seeded_ws):
    path = write_ass(seeded_ws, "testvid000001", 3, "[Script Info]\n")
    assert path.name == "clip_03.ass"
    assert path.parent == seeded_ws.renders_dir("testvid000001")
    assert path.read_text() == "[Script Info]\n"
