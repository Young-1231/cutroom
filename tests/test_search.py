"""Sanitized FTS search and capped span reads."""

from cutroom.index.search import read_span, search_transcript
from cutroom.types import Segment

VID = "testvid000001"


def test_search_hits_right_segment(seeded_ws):
    hits = search_transcript(seeded_ws, VID, "mariana")
    assert isinstance(hits, list) and len(hits) == 1
    assert isinstance(hits[0], Segment)
    assert "mariana trench" in hits[0].text


def test_search_survives_operators_and_quotes(seeded_ws):
    for q in ["don't AND (crash)", 'a "quoted" OR thing*', "NEAR/3 fish"]:
        res = search_transcript(seeded_ws, VID, q)
        assert isinstance(res, list | str)  # must not raise


def test_search_empty_query(seeded_ws):
    assert isinstance(search_transcript(seeded_ws, VID, "!!!"), str)


def test_read_span_renders_segments(seeded_ws):
    text = read_span(seeded_ws, VID, 0.0, 30.0)
    assert "(seg " in text and "[1.0–13.0]" in text
    assert "deep sea exploration" in text


def test_read_span_truncates_at_cap(seeded_ws):
    text = read_span(seeded_ws, VID, 0.0, 120.0, max_chars=120)
    assert len(text) <= 120
    assert "truncated" in text
    # Resume point is the START of the first un-shown segment (16.0s -> 00:16), so the
    # next read re-includes it whole instead of skipping it.
    assert "00:16" in text
    assert "(seg 2)" not in text  # segment 2 was not shown; it resumes there


def test_read_span_tiny_cap_does_not_loop(seeded_ws):
    # A cap smaller than the first segment must report the budget problem, not return
    # a marker whose resume point equals the span start (which would loop forever).
    text = read_span(seeded_ws, VID, 0.0, 120.0, max_chars=30)
    assert "budget too small" in text
    assert "truncated — read again from 00:00" not in text


def test_read_span_outside_speech(seeded_ws):
    assert "no transcript" in read_span(seeded_ws, VID, 13.2, 15.8)


def test_fts_stays_consistent_after_segment_update(seeded_ws):
    """The AFTER UPDATE trigger must re-index edited transcript text."""
    seg = seeded_ws.get_segments(VID)[0]
    seeded_ws.conn.execute("UPDATE segments SET text=? WHERE id=?", ("flamingo parade", seg.id))
    seeded_ws.conn.commit()
    assert not search_transcript(seeded_ws, VID, "welcome")  # old token gone
    hits = search_transcript(seeded_ws, VID, "flamingo")
    assert isinstance(hits, list) and len(hits) == 1 and hits[0].id == seg.id
    # FTS integrity check must pass (raises if the index is out of sync).
    seeded_ws.conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('integrity-check')")
