"""Fan-out highlight finding: scout windows in parallel, then merge.

A single editor scanning a long video serially burns budget and wall-clock. Instead we
split the video into windows (groups of scenes), run one scout agent per window
concurrently — each marking the best moments inside its window with a score — then
collect every moment, dedupe overlaps, rank globally, and assemble the final EDL from
the top picks. Because each moment already passed mark_moment's viewed-frame check in
its scout session, the assembled cuts keep the "every cut has receipts" guarantee.

Isolation contract (code-enforced, not prompt-trusted): scouts run with role="scout",
which strips propose_edl from their toolkit entirely — only this orchestrator can
assemble an EDL. Each scout is its own session; the only parent→child channel is the
task prompt string, and the only child→parent channel is the structured result
(moments with evidence), never raw context.
"""

from __future__ import annotations

import anyio

from cutroom.agent.prompts import task_scout_window
from cutroom.agent.runner import EditorResult, run_editor
from cutroom.db import Workspace
from cutroom.types import EDL, Cut, Moment, Scene

WINDOW_TARGET_SECONDS = 360.0  # aim for ~6-minute scout windows
MAX_WINDOWS = 8
MAX_CONCURRENCY = 4


def chunk_scenes(scenes: list[Scene], duration: float,
                 target_seconds: float = WINDOW_TARGET_SECONDS,
                 max_windows: int = MAX_WINDOWS) -> list[tuple[float, float]]:
    """Group contiguous scenes into ~target_seconds windows covering the whole video.

    Returns at most max_windows (t0, t1) ranges aligned to scene boundaries; the last
    window absorbs any remainder so coverage is complete.
    """
    if not scenes:
        return [(0.0, duration)] if duration > 0 else []
    n_windows = max(1, min(max_windows, round(duration / target_seconds) or 1))
    span = duration / n_windows
    windows: list[tuple[float, float]] = []
    start = scenes[0].t0
    for sc in scenes:
        # Close the current window once it reaches one span — unless we've already opened
        # all but the last window, which keeps the rest.
        if sc.t1 - start >= span and len(windows) < n_windows - 1:
            windows.append((start, sc.t1))
            start = sc.t1
    windows.append((start, scenes[-1].t1))
    return windows


def dedupe_rank_moments(moments: list[Moment], n: int) -> list[Moment]:
    """Greedy non-overlapping top-n by score, returned in chronological order."""
    ordered = sorted(moments, key=lambda m: (-m.score, m.t0))
    kept: list[Moment] = []
    for m in ordered:
        if len(kept) >= n:
            break
        if all(m.t1 <= k.t0 or m.t0 >= k.t1 for k in kept):  # no overlap with a kept pick
            kept.append(m)
    kept.sort(key=lambda m: m.t0)
    return kept


def _moments_in_window(result: EditorResult, t0: float, t1: float) -> list[Moment]:
    # A scout occasionally marks just outside its window; keep only moments whose
    # midpoint falls inside it so neighbouring scouts can't double-claim the same beat.
    return [m for m in result.moments if t0 <= (m.t0 + m.t1) / 2 <= t1]


async def highlights_fanout(
    ws: Workspace,
    video_id: str,
    n: int,
    vertical: bool,
    budget_per_window: int = 60_000,
    model: str | None = None,
    per_window_k: int = 2,
    max_concurrency: int = MAX_CONCURRENCY,
) -> EditorResult:
    meta = ws.get_video(video_id)
    if meta is None:
        raise ValueError(f"unknown video {video_id!r}")
    windows = chunk_scenes(ws.get_scenes(video_id), meta.duration)
    results: list[EditorResult | None] = [None] * len(windows)
    limiter = anyio.CapacityLimiter(max_concurrency)

    async def scout(i: int, w: tuple[float, float]) -> None:
        async with limiter:
            results[i] = await run_editor(
                ws, video_id, task_scout_window(w[0], w[1], per_window_k),
                budget_chars=budget_per_window, model=model, role="scout",
            )

    async with anyio.create_task_group() as tg:
        for i, w in enumerate(windows):
            tg.start_soon(scout, i, w)

    candidates: list[Moment] = []
    chars = 0
    turns = 0
    failures = 0
    for r, w in zip(results, windows, strict=True):
        if r is None:
            failures += 1
            continue
        chars += r.chars_used
        turns += r.num_turns
        if not r.ok:
            failures += 1
        candidates.extend(_moments_in_window(r, w[0], w[1]))

    top = dedupe_rank_moments(candidates, n)
    target = "vertical" if vertical else "landscape"
    edl = EDL(
        video_id=video_id,
        cuts=[Cut(t0=m.t0, t1=m.t1, label=m.reason[:60], evidence=m.evidence) for m in top],
        target=target, captions=True,
    )
    summary = (
        f"fan-out over {len(windows)} window(s): {len(candidates)} candidate moment(s),"
        f" kept top {len(top)}."
        + (f" {failures} window(s) returned nothing." if failures else "")
    )
    return EditorResult(
        final_text=summary,
        edl=edl if top else None,
        moments=top,
        chars_used=chars,
        num_turns=turns,
        ok=failures < len(windows),  # ok unless every window failed
        error=None if failures < len(windows) else "all scout windows failed",
    )


def highlights_fanout_sync(
    ws: Workspace, video_id: str, n: int, vertical: bool,
    budget_per_window: int = 60_000, model: str | None = None,
    per_window_k: int = 2, max_concurrency: int = MAX_CONCURRENCY,
) -> EditorResult:
    from functools import partial

    return anyio.run(partial(
        highlights_fanout, ws, video_id, n, vertical,
        budget_per_window=budget_per_window, model=model,
        per_window_k=per_window_k, max_concurrency=max_concurrency,
    ))
