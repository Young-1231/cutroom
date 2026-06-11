"""EDL validation and boundary snapping.

`validate_edl` is a pure function lazy-imported by the agent layer — keep this
module dependency-free beyond cutroom.types.
"""

from __future__ import annotations

from cutroom.types import EDL, Cut, Segment, Word

MIN_CUT_SECONDS = 1.0
MAX_CUT_SECONDS = 240.0
BOUNDS_SLACK = 0.05  # tolerate float drift at the very end of the video
EVIDENCE_SLACK = 1.0  # a viewed frame may sit just outside the cut
SNAP_WINDOW = 0.3  # max distance an edge moves to reach a word boundary
EDGE_PAD = 0.12  # breathing room added outward after snapping


def validate_edl(edl: EDL, duration: float, require_evidence: bool = True) -> list[str]:
    """Return human-readable problems with this EDL; [] means safe to render."""
    if not edl.cuts:
        return ["EDL has no cuts"]
    errors: list[str] = []
    for i, cut in enumerate(edl.cuts, start=1):
        if cut.t0 < 0.0 or cut.t1 > duration + BOUNDS_SLACK:
            errors.append(
                f"cut {i}: [{cut.t0:.2f}, {cut.t1:.2f}] out of bounds [0, {duration:.2f}]"
            )
        length = cut.t1 - cut.t0
        if not MIN_CUT_SECONDS <= length <= MAX_CUT_SECONDS:
            errors.append(
                f"cut {i}: length {length:.2f}s outside "
                f"[{MIN_CUT_SECONDS}, {MAX_CUT_SECONDS}]"
            )
        if require_evidence:
            if not cut.evidence.segment_ids:
                errors.append(f"cut {i}: no transcript evidence (segment_ids is empty)")
            lo, hi = cut.t0 - EVIDENCE_SLACK, cut.t1 + EVIDENCE_SLACK
            if not any(lo <= t <= hi for t in cut.evidence.frame_ts):
                errors.append(f"cut {i}: no viewed frame within [{lo:.2f}, {hi:.2f}]")
    for i in range(1, len(edl.cuts)):
        prev, cur = edl.cuts[i - 1], edl.cuts[i]
        if cur.t0 < prev.t0:
            errors.append(f"cut {i + 1}: cuts not sorted by t0")
        elif cur.t0 < prev.t1:
            errors.append(f"cut {i + 1}: overlaps cut {i}")
    return errors


def snap_edl(edl: EDL, segments: list[Segment], duration: float | None = None) -> EDL:
    """Snap cut edges off mid-word, pad outward, and keep cuts non-overlapping.

    Edges that land strictly inside a word move to the word's nearest boundary when
    that boundary is within SNAP_WINDOW. Every edge then gets EDGE_PAD of breathing
    room, clamped to [0, duration]. `duration` is the true media length when known
    (pass it — otherwise the last content timestamp is used, which can overrun EOF if
    an ASR segment's end exceeds the media). Cuts are sorted first; overlaps from
    padding are resolved at the midpoint; any cut that snapping degenerates to
    non-positive length is dropped. The result is not guaranteed to satisfy
    validate_edl (e.g. snapping can shrink a minimum-length cut) — callers should
    re-validate before rendering.
    """
    if not edl.cuts:
        return EDL(video_id=edl.video_id, cuts=[], target=edl.target, captions=edl.captions)
    words = sorted((w for s in segments for w in s.words), key=lambda w: w.t0)
    if duration is not None:
        upper = duration
    else:
        upper = max([s.t1 for s in segments] + [c.t1 for c in edl.cuts])
    ordered = sorted(edl.cuts, key=lambda c: c.t0)
    snapped = []
    for c in ordered:
        t0 = max(0.0, _snap_edge(c.t0, words) - EDGE_PAD)
        t1 = min(upper, _snap_edge(c.t1, words) + EDGE_PAD)
        snapped.append(Cut(t0=t0, t1=t1, label=c.label, evidence=c.evidence))
    for prev, cur in zip(snapped, snapped[1:], strict=False):
        if cur.t0 < prev.t1:
            mid = (prev.t1 + cur.t0) / 2.0
            # Clamp so neither side inverts even when the cuts heavily overlap.
            prev.t1 = max(prev.t0, mid)
            cur.t0 = min(cur.t1, mid)
    new_cuts = [c for c in snapped if c.t1 - c.t0 > 1e-3]
    return EDL(video_id=edl.video_id, cuts=new_cuts, target=edl.target, captions=edl.captions)


def _snap_edge(t: float, words: list[Word]) -> float:
    for w in words:
        if w.t0 < t < w.t1:
            d0, d1 = t - w.t0, w.t1 - t
            boundary, dist = (w.t0, d0) if d0 <= d1 else (w.t1, d1)
            return boundary if dist <= SNAP_WINDOW else t
    return t
