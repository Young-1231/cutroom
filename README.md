# cutroom

**A film-editor agent that logs your footage before it cuts.**
Local-first · GPU-free · every cut ships with receipts.

```
cutroom log https://youtube.com/watch?v=...   # ingest + index ("log the footage")
cutroom highlights <video> -n 3 --vertical    # agent finds & renders the best moments
cutroom highlights <video> --fanout           # scout long video in parallel windows
cutroom highlights <video> --plan             # review the edit plan before rendering
cutroom recipe podcast-shorts <video>         # named expert workflows (see `cutroom recipes`)
cutroom ask <video> "what did she say about pricing?"   # answers with [mm:ss] citations
cutroom chapters <video>                      # YouTube-ready chapter markers
cutroom cut <video> "make a 30s teaser focused on the demo failure"
cutroom cut <video> "tighter, 10s" --fork <session>   # branch a session: new cut style,
                                              # keeps the investigation already paid for
cutroom render <video> --target vertical      # re-render the saved EDL, no agent run
cutroom sessions <video>                      # past editor sessions (resume / fork any)
cutroom checkpoints <video>                   # EDL undo history; restore any state
```

## Why

Frontier agents are still bad at long-video work — not because the models can't reason,
but because the harness around them wastes the context window. On AgenticVBench
(May 2026, 100 real post-production tasks), the best frontier stack scores **31% vs
88.5%** for human experts, and **83% of repurposing failures are long-context
information loss**: the agent burns its budget dumping full transcripts and re-extracting
frames, and never reaches the actual edit.

Real editors solved this problem a century ago: **log the footage first** (shot lists,
transcripts, markers), cut from the logs, and go back to the footage only to verify.
cutroom encodes that discipline as architecture:

1. **The agent never sees the full transcript.** It gets a compact hierarchical *video
   map* (scenes ← shots ← word-timestamped transcript) plus budgeted, paged search tools.
2. **Every cut ships with receipts.** A cut is only accepted if it cites transcript
   segments *and* frames the agent actually viewed. Renders include a human-auditable
   `receipts.md` with thumbnails and quoted transcript.
3. **An explicit budget ledger** prices every tool call. The agent sees its remaining
   budget in every tool result and has to finish before it runs dry — by design, not by
   accident.

## How it works

```
            ┌─────────────────────  log (once per video)  ─────────────────────┐
 source ──► yt-dlp/ffmpeg ──► shots (scene detect) ──► faster-whisper (word ts) │
            └──► silences/loudness ──► scenes + one-liners ──► SQLite + FTS5 ◄──┘
                                                                   │
            ┌───────────────────────  agent loop  ─────────────────▼───────────┐
            │  get_video_map → search_transcript → read_transcript (paged)     │
            │  → view_frames (it really looks) → mark_moment (with evidence)   │
            │  → propose_edl  — every tool result carries the budget line      │
            └───────────────────────────────────────────────────┬──────────────┘
                                                                 ▼
                       EDL → snap to word boundaries → ffmpeg render
                       (9:16 crop, burned word-level captions) + receipts.md
```

- **Local-first, GPU-free.** Transcription is faster-whisper on CPU (Apple Silicon
  friendly); rendering is ffmpeg; the only remote calls are the agent's own reasoning
  (Claude via the [Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview) — reuses
  your Claude Code login, no separate API key needed).
- **No 8GB-GPU tower of models.** One LLM provider, one SQLite file, one binary
  dependency (ffmpeg).

## Real footage, real receipts

Run on *Duck and Cover* (1951, public domain, 9 min) — the instruction was
*"make one ~30 second teaser that opens on Bert the Turtle and ends right after the
atomic flash"*. The agent inspected the map, read three narrow transcript spans, viewed
seven frames, and proposed a 2-cut EDL totaling 30.5s — using **13.4k chars of tool
budget** (the full transcript alone would be ~9k, and on a 90-minute video this gap
becomes the whole ballgame).

![burned word-level captions](docs/demo-captioned-frame.jpg)

Every render ships a `receipts.md`:

> **Cut 1 — Bert the Turtle intro — animated song sequence** · 00:00–00:24
> **Why:** Teaser opener — full S1 Bert animated intro with "Dum-dum, name of Bert" song.
> Frames at t=10s (Bert close-up with flower) and t=19.8s (Bert in forest) confirm
> on-screen Bert animation throughout. Opens on natural pre-roll silence, clean scene
> boundary at 00:24.
> `> [00:05] Dum-dum, name of Bert, I'm dangerous…` + frame thumbnails

## Install

```bash
brew install ffmpeg yt-dlp        # macOS; Linux: apt install ffmpeg && pipx install yt-dlp
git clone https://github.com/Young-1231/cutroom && cd cutroom
uv sync
uv run cutroom --help
```

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and a logged-in
[Claude Code](https://www.claude.com/product/claude-code) (or `ANTHROPIC_API_KEY`).

> Caption burn-in needs an ffmpeg with libass. Some slim builds (including current
> Homebrew bottles) ship without it — cutroom detects this and automatically falls back
> to the bundled [static-ffmpeg](https://pypi.org/project/static-ffmpeg/), or set
> `CUTROOM_FFMPEG=/path/to/your/ffmpeg`.

## Usage

```bash
# 1. Log the footage (downloads, transcribes, indexes — prints the video map)
uv run cutroom log "https://www.youtube.com/watch?v=..."
uv run cutroom log ./lecture.mp4 --whisper-model small

# 2. Work with it
uv run cutroom list
uv run cutroom map 3f2a91
uv run cutroom ask 3f2a91 "what was the main objection raised in the Q&A?"
uv run cutroom highlights 3f2a91 -n 3 --vertical
uv run cutroom chapters 3f2a91
uv run cutroom cut 3f2a91 "60s recap of the live demo, keep the crowd reaction"
```

Outputs land in `~/.cutroom/media/<id>/renders/`: the clips, their `.ass` caption files,
and `receipts.md` — open it to see *why* each cut exists, with thumbnails of the exact
frames the agent inspected.

## Does the index actually pay off?

Ablation on *His Girl Friday* (1940, 92 min, famously dense dialogue — 133k chars of
transcript): same questions, same model, cutroom's map+tools vs the full transcript
pasted into context (what naive video agents do). Both arms answered all three
questions correctly; cutroom also frame-verified its claims.

| question (92-min film) | cutroom | full-transcript baseline | saving |
|---|---|---|---|
| What does Hildy plan to do after leaving? | 15.8k chars | 133.6k chars | **8.5×** |
| How does Walter get Bruce arrested? | 28.0k chars | 133.6k chars | **4.8×** |
| How is Earl Williams saved at the end? | 26.6k chars | 133.6k chars | **5.0×** |

The baseline's cost grows linearly with video length; cutroom's is capped by its budget
ledger no matter how long the footage is. (Small-N and self-judged — illustrative, not a
benchmark. Reproduce with `uv run python scripts/ablation.py <video> "<question>"`;
raw outputs in `docs/ablation-*.json`. The AgenticVBench scorecard is roadmap M2.)

## Working with the agent the way modern harnesses do

cutroom borrows the patterns that define this generation of agent tools (Claude Code,
Codex, OpenClaw):

- **Plan mode (human-in-the-loop).** `--plan` makes the editor produce its cut plan —
  each cut's time range, reason, and cited transcript — and stop. Editing is
  irreversible and subjective, so you review (and tweak `edl.json`) before a single
  frame renders, then apply with `cutroom render <video>`.
- **Recipes (reusable expert workflows).** Named, shareable editing skills:
  `cutroom recipe podcast-shorts <video>` packages "how an editor approaches a podcast"
  behind one name. `cutroom recipes` lists the built-ins (podcast-shorts, talk-highlights,
  teaser, quotes, tighten).
- **Fan-out (parallel sub-agents).** `--fanout` splits a long video into windows and
  runs one scout agent per window concurrently, then merges and globally ranks their
  picks — faster and cheaper than one agent scanning an hour serially, and each kept
  moment still carries its viewed-frame receipts. Scouts are isolated by construction:
  `propose_edl` is stripped from their toolkit entirely, so only the orchestrator can
  assemble an EDL.
- **Lifecycle gates + audit trail (hooks).** Budget and evidence rules are enforced at
  the harness layer, not just inside tool handlers: a PreToolUse gate denies
  investigation once the budget is spent and rejects any cut citing a frame the agent
  never actually viewed; every tool call, denial, and session summary lands in a
  per-video `trail.jsonl` with per-call costs.
- **Checkpoints (shadow-VCS over the EDL).** Every accepted or saved edit list becomes
  an immutable checkpoint — "undo to before that cut", independent of any session.
  `cutroom checkpoints <video> --diff cp_0002` shows cut-aware diffs
  (`~ cut 0 [68.46-87.82] -> [68.46-81.82]`); `cutroom restore` snapshots the current
  state first, so restores are themselves undoable.
- **Sessions: resume & fork.** Every run prints a session handle. `--resume` continues
  it with full memory; `--fork` branches it to try a different cut style without
  re-paying the investigation. In a real run, recutting a 20s clip into a 10s teaser
  via `--fork` cost 1,500 budget chars in 4 turns versus the parent's 12,489 in 13 —
  the fork reused the parent's viewed-frame receipts, and the evidence gate honored them.

## Design principles

- **Index-first, not context-first.** The transcript lives in SQLite+FTS5; the model gets
  a map and a search box, not a dump. This is the direct fix for the failure mode that
  dominates agentic video benchmarks.
- **Evidence-gated edits.** `propose_edl` rejects any cut that doesn't cite transcript
  segments and at least one frame the agent actually rendered to pixels. No vibes-based
  cutting.
- **Honest budgets.** Tool results are compact and paginated by construction; frames cost
  more than text; the ledger is visible to the model at every step.

## Status & roadmap

M0 — all verbs (`log` / `list` / `map` / `ask` / `highlights` / `chapters` / `cut` /
`render` / `sessions` / `checkpoints` / `restore`) implemented and verified end-to-end
on real footage; word-level burned captions (landscape + 9:16 vertical), adaptive scene
segmentation, EDL persistence, receipts, lifecycle hooks + audit trail, EDL checkpoints,
session resume/fork. 117 offline tests + live agent e2e runs, ruff-clean.

- **M1**: active-speaker-aware vertical crop (CPU face tracking); silence/filler-word trim
  presets; OTIO/EDL export for NLE handoff (DaVinci, Premiere).
- **M2**: AgenticVBench Repurpose-subset scorecard in CI + a budget-ablation chart
  (map+tools vs full-transcript baseline) — published honestly, whichever way it goes.
- **M3**: multi-video projects ("find every claim about X across my 10 lectures").

## License

MIT
