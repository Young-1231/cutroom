"""Core domain types for cutroom.

All times are float seconds from the start of the source video unless noted.
These dataclasses are the contract between ingest, index, agent, and render —
change them only with a matching migration in db.py.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class VideoMeta:
    id: str  # short content hash of the source URL/path
    source: str  # original URL or absolute file path
    title: str
    duration: float
    width: int = 0
    height: int = 0
    fps: float = 0.0
    created_at: str = ""  # ISO 8601


@dataclass
class Word:
    text: str
    t0: float
    t1: float


@dataclass
class Segment:
    """An ASR utterance with word-level timestamps."""

    id: int | None
    video_id: str
    t0: float
    t1: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Shot:
    """A visually continuous span (or a fixed window when footage is static)."""

    id: int | None
    video_id: str
    t0: float
    t1: float


@dataclass
class AudioEvent:
    """Non-speech audio signal. kind: "silence" | "loud". value: dB where relevant."""

    id: int | None
    video_id: str
    kind: str
    t0: float
    t1: float
    value: float = 0.0


@dataclass
class Scene:
    """A group of contiguous shots with a one-line summary; the unit of the video map."""

    id: int | None
    video_id: str
    t0: float
    t1: float
    title: str = ""
    summary: str = ""


@dataclass
class Evidence:
    """Receipts for a decision: transcript segments cited + frames actually viewed."""

    segment_ids: list[int] = field(default_factory=list)
    frame_ts: list[float] = field(default_factory=list)
    note: str = ""


@dataclass
class Moment:
    """A candidate moment registered by the agent before it commits to an EDL."""

    t0: float
    t1: float
    reason: str
    evidence: Evidence = field(default_factory=Evidence)
    score: float = 0.0


@dataclass
class Cut:
    t0: float
    t1: float
    label: str = ""
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class EDL:
    """Edit decision list: ordered cuts from one source video."""

    video_id: str
    cuts: list[Cut]
    target: str = "landscape"  # "landscape" (source aspect) | "vertical" (9:16 crop)
    captions: bool = True


def edl_to_dict(edl: EDL) -> dict[str, Any]:
    return asdict(edl)


def evidence_from_dict(d: dict[str, Any]) -> Evidence:
    return Evidence(
        segment_ids=[int(s) for s in d.get("segment_ids", [])],
        frame_ts=[float(t) for t in d.get("frame_ts", [])],
        note=str(d.get("note", "")),
    )


def cut_from_dict(d: dict[str, Any]) -> Cut:
    return Cut(
        t0=float(d["t0"]),
        t1=float(d["t1"]),
        label=str(d.get("label", "")),
        evidence=evidence_from_dict(d.get("evidence", {})),
    )


def edl_from_dict(d: dict[str, Any]) -> EDL:
    return EDL(
        video_id=str(d["video_id"]),
        cuts=[cut_from_dict(c) for c in d.get("cuts", [])],
        target=str(d.get("target", "landscape")),
        captions=bool(d.get("captions", True)),
    )
