# Launch post drafts (2026-06-12)

Post when YOU decide. HN prefers weekday mornings US-Eastern; Reddit is more
forgiving. Don't post to both on the same day — stagger so you can respond to
comments properly.

---

## Show HN

**Title** (80-char limit, no marketing tone):

> Show HN: Cutroom – a video-editing agent that logs footage before it cuts

**Text:**

I built cutroom because frontier agents are still bad at long-video editing —
not because models can't reason, but because the harness wastes the context
window. On AgenticVBench (100 real post-production tasks), the best stack
scores 31% vs 88.5% for human experts, and 83% of repurposing failures are
long-context information loss: the agent burns its budget dumping transcripts
and never reaches the edit.

Real editors solved this a century ago: log the footage first, cut from the
logs, go back to the footage only to verify. cutroom encodes that as
architecture:

- The agent never sees the full transcript. It gets a hierarchical video map
  (scenes ← shots ← word-timestamped transcript) plus budgeted, paged search
  tools. On a 92-minute film that's a 4.8–8.5× budget saving over pasting the
  transcript (ablation in the README).
- Every cut ships with receipts: a cut is only accepted if it cites transcript
  segments AND frames the agent actually rendered to pixels. The receipts.md
  shows you exactly why each cut exists.
- An explicit budget ledger prices every tool call; the agent sees its
  remaining budget in every result.

It's also a playground for current agent-harness patterns: plan mode, skills
as markdown files with progressive disclosure, parallel scout fan-out, mid-run
steering (type guidance while it works), a fresh-eyes critic round, session
fork (recut without re-paying the investigation), shadow-VCS checkpoints over
the edit list, and a full audit trail.

Local-first and GPU-free: transcription is faster-whisper on CPU, rendering is
ffmpeg; the only remote calls are the agent's own reasoning (Claude via the
Agent SDK — reuses your Claude Code login).

Install: uv tool install cutroom
Repo: https://github.com/Young-1231/cutroom

Honest limitations: quality judgment is subjective and not benchmarked — the
scorecard in the repo only measures what's mechanically falsifiable (duration
adherence, receipts coverage, boundary cleanliness). One LLM provider for now.
M1 roadmap is speaker-aware vertical crop and OTIO export for NLE handoff.

---

## r/LocalLLaMA

**Title:**

> cutroom: local-first, GPU-free video editing agent — every cut cites the frames it actually looked at

**Text:**

Most "AI clip" tools either need an 8GB-GPU tower of models or are closed
SaaS. cutroom is neither: faster-whisper on CPU (Apple Silicon friendly),
ffmpeg for rendering, SQLite+FTS5 for the index, one LLM provider for the
agent loop.

The core idea: the agent never reads the full transcript. It works like a
film editor — from logs (a compact hierarchical video map + budgeted search
tools), returning to actual frames only to verify what it's about to cut.
Every accepted cut must cite transcript segments and frames the agent really
viewed; renders include a receipts.md with thumbnails so you can audit why
each cut exists.

Fun harness bits: you can type guidance mid-run to steer it, fork a session
to try a different cut style without re-paying the investigation, --verify
spawns a fresh-context critic that re-checks every boundary, and
`cutroom trail` shows every tool call, charge, and gate denial.

The model side is Claude via the Agent SDK (reuses a Claude Code login). Yes,
that's a remote call — the local-first claim is about media: your video never
leaves the machine, the model only sees the map, the snippets it searches,
and the frames it views. Fully-local model support is an open question — the
tool surface is small (7 tools), so a strong local model could plausibly run
it; happy to take pointers.

GitHub: https://github.com/Young-1231/cutroom (MIT, demo GIF in the README)

---

## X/Twitter (thread opener)

cutroom: a film-editor agent that logs footage before it cuts.

- never reads the full transcript (4.8–8.5× cheaper on a 92-min film)
- every cut ships with receipts — the frames it ACTUALLY looked at
- local-first, GPU-free: CPU whisper + ffmpeg
- steer it mid-run, fork sessions, fresh-eyes critic

uv tool install cutroom
[demo GIF attached]
