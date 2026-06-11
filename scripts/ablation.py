"""Budget ablation: index-first agent (cutroom) vs full-transcript-in-context baseline.

For each question, two arms answer it against the same logged video:
  A. cutroom  — run_editor with the map + budgeted tools; cost = ledger chars actually
                consumed by tool results (what the model had to read).
  B. baseline — the entire transcript is pasted into the prompt (what naive video agents
                do); cost = transcript chars (+question), one shot, no tools.

Usage: uv run python scripts/ablation.py <video_ref> "<q1>" ["<q2>" ...]
Writes docs/ablation-<video_id>.json and prints a markdown table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from cutroom.agent.prompts import task_ask
from cutroom.agent.runner import DEFAULT_MODEL, run_editor_sync
from cutroom.db import Workspace


def full_transcript(ws: Workspace, video_id: str) -> str:
    return "\n".join(
        f"[{s.t0:.1f}-{s.t1:.1f}] {s.text.strip()}" for s in ws.get_segments(video_id)
    )


def baseline_ask(transcript: str, question: str) -> tuple[str, int]:
    prompt = (
        "Here is the full transcript of a video with [start-end] second timestamps.\n\n"
        f"{transcript}\n\n"
        f"Question: {question}\n"
        "Answer concisely with [mm:ss] citations."
    )

    async def go() -> str:
        out = ""
        opts = ClaudeAgentOptions(model=DEFAULT_MODEL, max_turns=1, setting_sources=[])
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, ResultMessage) and msg.result:
                out = msg.result
        return out

    return anyio.run(go), len(prompt)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    ref, questions = sys.argv[1], sys.argv[2:]
    ws = Workspace()
    meta = ws.resolve_video(ref)
    if meta is None:
        sys.exit(f"unknown video {ref!r}")
    transcript = full_transcript(ws, meta.id)
    rows = []
    for q in questions:
        print(f"— {q!r}", file=sys.stderr)
        a = run_editor_sync(ws, meta.id, task_ask(q), budget_chars=60_000,
                            output_language="English")
        b_text, b_chars = baseline_ask(transcript, q)
        rows.append({
            "question": q,
            "cutroom": {"chars": a.chars_used, "turns": a.num_turns, "answer": a.final_text},
            "baseline": {"chars": b_chars, "answer": b_text},
        })
    result = {
        "video": {"id": meta.id, "title": meta.title, "duration": meta.duration},
        "transcript_chars": len(transcript),
        "model": DEFAULT_MODEL,
        "rows": rows,
    }
    out = Path("docs") / f"ablation-{meta.id}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    mins = meta.duration / 60
    print(f"\n### {meta.title} — {mins:.0f} min, transcript {len(transcript):,} chars\n")
    print("| question | cutroom chars | baseline chars | ratio |")
    print("|---|---|---|---|")
    for r in rows:
        c, b = r["cutroom"]["chars"], r["baseline"]["chars"]
        print(f"| {r['question'][:48]}… | {c:,} | {b:,} | {b / max(c, 1):.1f}x |")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
