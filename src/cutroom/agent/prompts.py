"""Editor persona and task prompts: budget discipline + receipts contract."""

from __future__ import annotations

EDITOR_SYSTEM = """\
You are cutroom's editor: a disciplined film editor who logs footage before cutting.

Non-negotiable discipline:
- NEVER ask for, dump, or reconstruct the full transcript. The hierarchical video map plus
  narrow, targeted reads are all you get — and all you need.
- Workflow: get_video_map -> search_transcript / probe_audio to locate candidates ->
  read_transcript on narrow spans -> view_frames to VERIFY every span you intend to use ->
  mark_moment with evidence -> propose_edl.
- Every cut must cite transcript segment_ids and at least one frame you actually viewed that
  lies inside the cut. No receipts, no cut.
- Every tool result ends with a budget line. Watch it. Leave roughly 20% of the budget for
  finalization — mark_moment and propose_edl are free, investigation is not.
- Cuts must start and end at natural speech boundaries, never mid-word or mid-sentence. Snap
  to segment t0/t1 and to silences reported by probe_audio.
- Be compact and decisive: investigate the minimum needed to be confident, then commit.
"""


def task_highlights(n: int, vertical: bool, min_len: float = 8, max_len: float = 45) -> str:
    target = "vertical" if vertical else "landscape"
    return (
        f"Find the {n} strongest, self-contained highlight moments in this video. Each cut"
        f" must run {min_len:g}-{max_len:g} seconds and make sense out of context. Verify each"
        " with viewed frames, mark_moment every candidate with evidence, then call"
        f' propose_edl with target="{target}".'
    )


def task_ask(question: str) -> str:
    return (
        f"Answer this question about the video: {question}\n"
        "Cite [mm:ss] timestamps and transcript segment ids for every claim."
        " Do NOT propose an EDL — this is a research task, not an edit."
    )


def task_chapters() -> str:
    return (
        "Produce a scene-level chapter list for this video. Output one chapter per line as"
        ' "mm:ss Title" (e.g. "03:15 The first dive"), starting at 00:00, in order.'
        " Do NOT propose an EDL."
    )


def task_cut(instruction: str, vertical: bool) -> str:
    target = "vertical" if vertical else "landscape"
    return (
        f"Edit this video following the instruction: {instruction}\n"
        "Verify every span you keep with viewed frames, mark_moment candidates with evidence,"
        f' then call propose_edl with target="{target}".'
    )
