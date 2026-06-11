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
    assert len(text) < 300
    assert "[truncated at" in text


def test_read_span_outside_speech(seeded_ws):
    assert "no transcript" in read_span(seeded_ws, VID, 13.2, 15.8)
