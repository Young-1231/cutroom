"""In-process MCP tools: budgeted, evidence-collecting primitives for the editor agent.

Every tool result ends with the ledger's budget line; once the budget is exhausted,
investigation tools refuse and point at finalization (mark_moment / propose_edl).
Cross-module imports (cutroom.index, cutroom.render) are lazy so this module works
while those are built in parallel — each call degrades to a direct-db fallback.
"""

from __future__ import annotations

import base64
import sqlite3
import subprocess
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from cutroom.agent.budget import Ledger
from cutroom.db import Workspace
from cutroom.types import EDL, Evidence, Moment, Segment, edl_from_dict, edl_to_dict

EXHAUSTED_MSG = "budget exhausted — finalize with mark_moment/propose_edl now"
MAX_FRAMES_PER_CALL = 6
READ_SPAN_CAP = 2500
FRAME_TS_TOLERANCE = 0.05  # seconds; how close a cited frame must be to a viewed one
MIN_CUT_SECONDS = 1.0
MAX_CUT_SECONDS = 240.0

_NO_ARGS = {"type": "object", "properties": {}}
_SPAN_ARGS = {
    "type": "object",
    "properties": {
        "t0": {"type": "number", "description": "span start, seconds"},
        "t1": {"type": "number", "description": "span end, seconds"},
    },
    "required": ["t0", "t1"],
}
_SEARCH_ARGS = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "FTS5 match query, e.g. 'volcano OR ash'"},
        "limit": {"type": "integer", "description": "max hits, default 8"},
    },
    "required": ["query"],
}
_VIEW_ARGS = {
    "type": "object",
    "properties": {
        "timestamps": {
            "type": "array",
            "items": {"type": "number"},
            "description": f"seconds to grab frames at, max {MAX_FRAMES_PER_CALL} per call",
        },
    },
    "required": ["timestamps"],
}
_MARK_ARGS = {
    "type": "object",
    "properties": {
        "t0": {"type": "number"},
        "t1": {"type": "number"},
        "reason": {"type": "string", "description": "why this moment earns a cut"},
        "segment_ids": {"type": "array", "items": {"type": "integer"}},
        "frame_ts": {
            "type": "array",
            "items": {"type": "number"},
            "description": "timestamps of frames you actually viewed inside this moment",
        },
        "score": {"type": "number", "description": "relative strength, optional"},
    },
    "required": ["t0", "t1", "reason", "segment_ids", "frame_ts"],
}
_EDL_ARGS = {
    "type": "object",
    "properties": {
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "t0": {"type": "number"},
                    "t1": {"type": "number"},
                    "label": {"type": "string"},
                    "segment_ids": {"type": "array", "items": {"type": "integer"}},
                    "frame_ts": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["t0", "t1", "segment_ids", "frame_ts"],
            },
        },
        "target": {"type": "string", "description": '"landscape" or "vertical"'},
        "captions": {"type": "boolean"},
    },
    "required": ["cuts"],
}


def _mmss(t: float) -> str:
    s = max(0, int(t))
    return f"{s // 60:02d}:{s % 60:02d}"


def _text_only(text: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


def _render_segments(segs: list[Segment]) -> str:
    return "\n".join(f"[{_mmss(s.t0)}-{_mmss(s.t1)}] (seg {s.id}) {s.text}" for s in segs)


def _basic_validate(edl: EDL, duration: float) -> list[str]:
    """Fallback EDL checks while cutroom.render.edl is not available."""
    errors: list[str] = []
    if not edl.cuts:
        errors.append("EDL must contain at least one cut")
    prev_t1: float | None = None
    for i, c in enumerate(edl.cuts):
        tag = f"cut {i} [{c.t0:.2f}-{c.t1:.2f}]"
        if c.t1 <= c.t0:
            errors.append(f"{tag}: t1 must be greater than t0")
            continue
        length = c.t1 - c.t0
        if not MIN_CUT_SECONDS <= length <= MAX_CUT_SECONDS:
            errors.append(
                f"{tag}: length {length:.1f}s outside {MIN_CUT_SECONDS}-{MAX_CUT_SECONDS}s"
            )
        if c.t0 < 0 or (duration > 0 and c.t1 > duration + 0.05):
            errors.append(f"{tag}: out of bounds 0-{duration:.1f}s")
        if prev_t1 is not None and c.t0 < prev_t1:
            errors.append(f"{tag}: overlaps or is out of order with the previous cut")
        prev_t1 = c.t1
    return errors


def make_toolkit(ws: Workspace, video_id: str, ledger: Ledger, registry: dict) -> dict[str, Any]:
    """Build the cutroom MCP server bound to one video, one ledger, one evidence registry.

    Returns {"server": McpSdkServerConfig, "tool_names": [full mcp names],
    "handlers": {short name: async handler}} — handlers are exposed for direct
    invocation in tests and debugging.
    """
    registry.setdefault("viewed_frames", [])
    registry.setdefault("moments", [])
    registry.setdefault("edl", None)

    def reply(text: str, label: str | None = None, is_error: bool = False) -> dict[str, Any]:
        # Charge before rendering the line so the model sees post-charge remaining budget.
        if label is not None:
            ledger.charge(label, len(text))
        return _text_only(f"{text}\n{ledger.line()}", is_error=is_error)

    @tool(
        "get_video_map",
        "Hierarchical map of the video (chapters/scenes with one-line summaries and stats)."
        " Always start here.",
        _NO_ARGS,
    )
    async def get_video_map(args: dict[str, Any]) -> dict[str, Any]:
        if ledger.exhausted:
            return _text_only(EXHAUSTED_MSG)
        try:
            from cutroom.index.map import render_video_map

            text = str(render_video_map(ws, video_id))
        except ImportError:
            text = (
                "video map unavailable (cutroom.index not installed yet); explore with"
                " search_transcript / read_transcript / probe_audio instead"
            )
        return reply(text, "get_video_map")

    @tool(
        "search_transcript",
        "Full-text search the transcript; compact hits with [mm:ss] stamps and segment ids.",
        _SEARCH_ARGS,
    )
    async def search_transcript(args: dict[str, Any]) -> dict[str, Any]:
        if ledger.exhausted:
            return _text_only(EXHAUSTED_MSG)
        query = str(args["query"])
        limit = int(args.get("limit") or 8)
        try:
            from cutroom.index.search import search_transcript as _search
        except ImportError:
            try:
                hits = ws.fts_search(video_id, query, limit=limit)
            except sqlite3.OperationalError as e:
                return reply(f"bad search query {query!r}: {e}", "search_transcript", True)
            text = _render_segments(hits) if hits else f"no transcript hits for {query!r}"
        else:
            res = _search(ws, video_id, query, limit=limit)
            if isinstance(res, str):
                text = res
            else:
                hits = list(res)
                text = _render_segments(hits) if hits else f"no transcript hits for {query!r}"
        return reply(text, "search_transcript")

    @tool(
        "read_transcript",
        "Read the word-timestamped transcript for a narrow span. Hard-capped per call;"
        " keep spans tight.",
        _SPAN_ARGS,
    )
    async def read_transcript(args: dict[str, Any]) -> dict[str, Any]:
        if ledger.exhausted:
            return _text_only(EXHAUSTED_MSG)
        t0, t1 = float(args["t0"]), float(args["t1"])
        max_chars = min(READ_SPAN_CAP, ledger.remaining)
        try:
            from cutroom.index.search import read_span

            text = str(read_span(ws, video_id, t0, t1, max_chars=max_chars))
        except ImportError:
            segs = ws.get_segments(video_id, t0, t1)
            text = _render_segments(segs)[:max_chars]
            if not text:
                text = f"no transcript between {_mmss(t0)} and {_mmss(t1)}"
        return reply(text, "read_transcript")

    @tool(
        "view_frames",
        f"Extract and SEE actual video frames at the given timestamps (max"
        f" {MAX_FRAMES_PER_CALL}/call). Expensive ({Ledger.FRAME_COST} chars each) — use to"
        " verify spans you intend to cut.",
        _VIEW_ARGS,
    )
    async def view_frames(args: dict[str, Any]) -> dict[str, Any]:
        if ledger.exhausted:
            return _text_only(EXHAUSTED_MSG)
        stamps = [float(t) for t in args.get("timestamps", [])][:MAX_FRAMES_PER_CALL]
        if not stamps:
            return _text_only(f"view_frames needs at least one timestamp\n{ledger.line()}", True)
        src = ws.source_path(video_id)
        if not src.exists():
            return _text_only(f"source media missing: {src}\n{ledger.line()}", True)
        frames_dir = ws.frames_dir(video_id)
        lines: list[str] = []
        images: list[dict[str, Any]] = []
        for t in stamps:
            out = frames_dir / f"f{t:09.3f}.jpg"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                 "-vf", "scale=768:-2", "-q:v", "7", str(out)],
                capture_output=True,
            )
            if proc.returncode != 0 or not out.exists():
                lines.append(f"[{_mmss(t)}] t={t:.2f}s — frame extraction failed")
                continue
            ledger.charge("view_frames", Ledger.FRAME_COST)
            registry["viewed_frames"].append(t)
            lines.append(f"[{_mmss(t)}] t={t:.2f}s -> {out}")
            images.append({
                "type": "image",
                "data": base64.b64encode(out.read_bytes()).decode("ascii"),
                "mimeType": "image/jpeg",
            })
        text = "\n".join(lines) + f"\n{ledger.line()}"
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}, *images]}
        if not images:
            result["is_error"] = True
        return result

    @tool(
        "probe_audio",
        "Cheap audio summary for a span: silences, loud events, speech density.",
        _SPAN_ARGS,
    )
    async def probe_audio(args: dict[str, Any]) -> dict[str, Any]:
        if ledger.exhausted:
            return _text_only(EXHAUSTED_MSG)
        t0, t1 = float(args["t0"]), float(args["t1"])
        span = max(t1 - t0, 1e-9)
        speech = sum(
            min(s.t1, t1) - max(s.t0, t0) for s in ws.get_segments(video_id, t0, t1)
        )
        lines = [
            f"audio {_mmss(t0)}-{_mmss(t1)}: speech density {speech / span:.0%}"
            f" ({speech:.1f}s speech / {span:.1f}s span)"
        ]
        events = [e for e in ws.get_audio_events(video_id) if e.t1 > t0 and e.t0 < t1]
        for e in events:
            lines.append(f"  {e.kind} [{_mmss(e.t0)}-{_mmss(e.t1)}] {e.value:.1f} dB")
        if not events:
            lines.append("  no audio events in span")
        return reply("\n".join(lines), "probe_audio")

    @tool(
        "mark_moment",
        "Register a candidate moment with evidence. frame_ts must be frames you actually"
        " viewed, inside the moment. Free.",
        _MARK_ARGS,
    )
    async def mark_moment(args: dict[str, Any]) -> dict[str, Any]:
        t0, t1 = float(args["t0"]), float(args["t1"])
        reason = str(args["reason"])
        segment_ids = [int(s) for s in args.get("segment_ids", [])]
        frame_ts = [float(t) for t in args.get("frame_ts", [])]
        score = float(args.get("score") or 0)
        problems: list[str] = []
        if t1 <= t0:
            problems.append(f"t1 ({t1:.2f}) must be greater than t0 ({t0:.2f})")
        viewed = registry["viewed_frames"]
        for t in frame_ts:
            if not any(abs(t - v) <= FRAME_TS_TOLERANCE for v in viewed):
                problems.append(
                    f"frame {t:.2f}s was never viewed — call view_frames([{t:.2f}]) first"
                )
            elif not t0 - 1.0 <= t <= t1 + 1.0:
                problems.append(
                    f"frame {t:.2f}s is outside the moment window"
                    f" [{t0 - 1.0:.2f}, {t1 + 1.0:.2f}]"
                )
        if problems:
            return reply("mark_moment rejected:\n- " + "\n- ".join(problems), is_error=True)
        registry["moments"].append(
            Moment(t0=t0, t1=t1, reason=reason, score=score,
                   evidence=Evidence(segment_ids=segment_ids, frame_ts=frame_ts))
        )
        return reply(
            f"moment #{len(registry['moments'])} marked [{_mmss(t0)}-{_mmss(t1)}]"
            f" ({len(segment_ids)} segments, {len(frame_ts)} frames cited)"
        )

    @tool(
        "propose_edl",
        "Submit the final edit decision list. Each cut needs segment_ids + viewed frame_ts."
        " Returns precise errors to fix, or confirms acceptance. Free.",
        _EDL_ARGS,
    )
    async def propose_edl(args: dict[str, Any]) -> dict[str, Any]:
        try:
            edl = edl_from_dict({
                "video_id": video_id,
                "cuts": [
                    {
                        "t0": c["t0"], "t1": c["t1"], "label": c.get("label", ""),
                        "evidence": {
                            "segment_ids": c.get("segment_ids", []),
                            "frame_ts": c.get("frame_ts", []),
                        },
                    }
                    for c in args.get("cuts", [])
                ],
                "target": str(args.get("target") or "landscape"),
                "captions": bool(args.get("captions", True)),
            })
        except (KeyError, TypeError, ValueError) as e:
            return reply(f"malformed cuts: {e!r} — each cut needs t0/t1 numbers", is_error=True)
        meta = ws.get_video(video_id)
        duration = meta.duration if meta else 0.0
        try:
            from cutroom.render.edl import validate_edl

            errors = [str(e) for e in (validate_edl(edl, duration, require_evidence=True) or [])]
        except ImportError:
            errors = _basic_validate(edl, duration)
        if errors:
            return reply("EDL rejected — fix and retry:\n- " + "\n- ".join(errors), is_error=True)
        registry["edl"] = edl_to_dict(edl)
        total = sum(c.t1 - c.t0 for c in edl.cuts)
        return reply(
            f"EDL accepted: {len(edl.cuts)} cuts, {total:.1f}s total,"
            f" target={edl.target}, captions={edl.captions}"
        )

    tool_defs = [get_video_map, search_transcript, read_transcript, view_frames,
                 probe_audio, mark_moment, propose_edl]
    server = create_sdk_mcp_server("cutroom", version="0.1.0", tools=tool_defs)
    return {
        "server": server,
        "tool_names": [f"mcp__cutroom__{t.name}" for t in tool_defs],
        "handlers": {t.name: t.handler for t in tool_defs},
    }
