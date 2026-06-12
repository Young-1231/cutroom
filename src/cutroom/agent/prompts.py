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


def task_scout_window(t0: float, t1: float, k: int, focus: str = "") -> str:
    """Scout one time window for the best moments — used by the fan-out orchestrator.

    The scout marks moments with scores but does NOT propose an EDL; the orchestrator
    collects moments from every window, ranks them globally, and assembles the final cut.
    """
    extra = f" {focus}" if focus else ""
    mm0, mm1 = f"{int(t0 // 60):02d}:{int(t0 % 60):02d}", f"{int(t1 // 60):02d}:{int(t1 % 60):02d}"
    return (
        f"You are scouting ONLY the window [{mm0}–{mm1}] ({t0:.1f}s–{t1:.1f}s) of this video"
        f" for highlights. Ignore everything outside it. Find up to {k} strong, self-contained"
        f" moments inside the window.{extra} Verify each with viewed frames, then mark_moment"
        " every candidate with evidence and a score from 0.0 to 1.0 (1.0 = unmissable)."
        " Do NOT propose an EDL — marking moments is your final action."
    )


def task_verify(cuts: list[dict], task_summary: str = "") -> str:
    """Independent review of an accepted EDL — run in a FRESH session (clean context),
    so the critique is fresh-eyes, not self-grading inside the editor's own window."""
    lines = []
    for i, c in enumerate(cuts):
        ev = c.get("evidence") or {}
        lines.append(
            f"- cut {i}: [{c['t0']:.2f}s–{c['t1']:.2f}s] {c.get('label', '')!r}"
            f" (cites segments {ev.get('segment_ids', [])},"
            f" frames {ev.get('frame_ts', [])})"
        )
    task = f' for the task: "{task_summary}"' if task_summary else ""
    return (
        f"You are reviewing an edit decision list another editor produced{task}."
        " You did NOT make these cuts — judge them on the footage, not on trust.\n"
        "Cuts under review:\n" + "\n".join(lines) + "\n\n"
        "For EACH cut: read the transcript at both boundaries (does it start and end"
        " on clean speech edges, no mid-sentence entry?), view at least one frame"
        " inside the cut (does the picture match the label?), and judge whether the"
        " cut serves the task. Use probe_audio where boundaries look suspicious.\n"
        "Then call submit_review exactly once with a verdict per cut: ok=true, or"
        " ok=false plus a specific, actionable issue (what is wrong and how to fix"
        " it, e.g. 'starts mid-sentence — push t0 to 68.4 where the sentence"
        " begins'). Flag only real problems a viewer would notice; style nitpicks"
        " are not issues."
    )


def task_revise(issues: list[str]) -> str:
    """One bounded revision round, resumed into the editor's own session."""
    return (
        "[REVIEW — an independent reviewer checked your accepted EDL with fresh eyes]\n"
        "Flagged issues:\n" + "\n".join(f"- {i}" for i in issues) + "\n"
        "Fix exactly these issues and call propose_edl with the corrected EDL."
        " Keep every cut the reviewer did not flag. View any new frame you cite."
    )


def task_cut(instruction: str, vertical: bool) -> str:
    target = "vertical" if vertical else "landscape"
    return (
        f"Edit this video following the instruction: {instruction}\n"
        "Verify every span you keep with viewed frames, mark_moment candidates with evidence,"
        f' then call propose_edl with target="{target}".'
    )
