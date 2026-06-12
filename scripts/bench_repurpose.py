"""Repurpose scorecard: run the bench tasks against a logged video, print + save results.

Usage:
  uv run python scripts/bench_repurpose.py <video_ref> [tasks.json] [--model M]

The video must already be logged (`cutroom log <url-or-file>`). Mechanical metrics
only — see cutroom.bench for what is (and deliberately isn't) measured. Writes
docs/bench-repurpose-<video_id>.json and prints a markdown table; in GitHub Actions
the table also lands in the step summary.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from cutroom.bench import load_tasks, markdown_table, run_bench
from cutroom.db import Workspace


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    model = None
    if "--model" in sys.argv:
        model = sys.argv[sys.argv.index("--model") + 1]
    if not args:
        sys.exit(__doc__)
    ref = args[0]
    tasks_path = Path(args[1]) if len(args) > 1 else Path("bench/repurpose.json")
    ws = Workspace()
    meta = ws.resolve_video(ref)
    if meta is None:
        sys.exit(f"unknown video {ref!r} — log it first: cutroom log <url-or-file>")
    tasks = load_tasks(tasks_path)
    print(f"benching {meta.title or meta.id} — {len(tasks)} tasks from {tasks_path}\n")
    rows = run_bench(ws, meta.id, tasks, model=model)
    table = markdown_table(rows)
    print(table)
    passed = sum(1 for r in rows if r["score"].get("ok"))
    summary = f"\n**{passed}/{len(rows)} tasks passed** (mechanical checks only)"
    print(summary)
    out = Path("docs") / f"bench-repurpose-{meta.id}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "video": meta.id, "title": meta.title, "duration": meta.duration,
        "ran": datetime.now(UTC).isoformat(timespec="seconds"),
        "tasks": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nsaved → {out}")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(f"## Repurpose scorecard — {meta.title or meta.id}\n\n"
                    + table + "\n" + summary + "\n")


if __name__ == "__main__":
    main()
