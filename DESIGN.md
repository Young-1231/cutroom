# cutroom — Design

> A film-editor agent that **logs your footage before it cuts**.
> Local-first, GPU-free, and every cut ships with receipts.

## 1. Why this exists (evidence, verified 2026-06-11)

- **AgenticVBench** (arXiv 2605.27705, May 2026): frontier agent stacks score 31% vs human
  experts' 88.5% on real video post-production tasks. **83% of Repurpose-family failures are
  long-context information loss** — agents burn their rollout budget on full Whisper dumps and
  repeated frame extraction, never reaching the assembly step.
- **VideoOdyssey** (arXiv 2605.22907): model performance peaks at <3min continuous reasoning
  spans and degrades after — the bottleneck is structural, so an engineering layer
  (index + memory + budget) has real leverage that bigger models alone don't fix.
- **Competitive gap**: HKUDS/VideoAgent (748★) is agentic but needs an 8GB GPU + 4 LLM
  providers; AI-Youtube-Shorts-Generator (3.8k★) is a fixed pipeline (transcript-rank only, no
  visual verification, not agentic). Commercial tools (Opus Clip) are closed SaaS.
  Nobody ships: **local-first + GPU-free + genuinely agentic + index-first + evidence-grounded**.

## 2. The core idea

Real editors don't scrub raw footage linearly — they **log** it first (shot lists, transcripts,
markers), then cut from the logs, returning to the footage only to verify. cutroom encodes that
discipline as architecture:

1. The agent **never sees the full transcript**. It gets a compact hierarchical *video map*
   (chapters ← scenes ← shots ← word-level transcript) and budgeted, paged search tools.
2. Every cut decision must carry **receipts**: transcript segment IDs + timestamps of frames the
   agent actually viewed. Renders include a human-auditable receipts report.
3. An explicit **budget ledger** prices every tool call; the agent sees its remaining budget and
   tool outputs are compact and paginated by construction.

## 3. Architecture

```
src/cutroom/
  types.py        # Shot, Segment, AudioEvent, Scene, Moment, Cut, EDL, Receipt
  db.py           # SQLite + FTS5 schema, CRUD, workspace layout
  ingest/         # "logging the footage"
    fetch.py      #   yt-dlp URL / local file → normalized mp4 + mono 16k wav
    shots.py      #   ffmpeg scene detection → shots (fallback: fixed windows)
    asr.py        #   faster-whisper word-level transcription (CPU/Metal, GPU-free)
    audio.py      #   ffmpeg ebur128 + silencedetect → loudness curve, silences, music flags
    logger.py     #   orchestrates ingest → populates db, builds scenes + map
  index/
    map.py        #   hierarchical video map builder (scenes from shots+pauses; 1-line scene
                  #   summaries via a cheap haiku pass at log time)
    search.py     #   FTS5 transcript search + budgeted paged reads
  agent/
    budget.py     #   ledger: char/token pricing per tool result, remaining-budget readout
    tools.py      #   in-process MCP tools (см. §4)
    prompts.py    #   editor persona + budget discipline + receipts contract
    runner.py     #   ClaudeSDKClient wiring (Claude Agent SDK, reuses Claude Code auth)
  render/
    edl.py        #   EDL validation: monotonic, in-bounds, min/max, snap to word/shot boundaries
    captions.py   #   word-timestamp ASS subtitles (burned via libass)
    ffmpeg.py     #   EDL → mp4: trim/concat, 9:16 smart-crop (MVP: center), fades
    receipts.py   #   receipts.md: per-cut why + transcript excerpt + viewed-frame thumbnails
  cli.py          # typer: log / highlights / ask / chapters / cut
```

### Workspace
`~/.cutroom/` (override: `CUTROOM_HOME`): `library.db` + `media/<video_id>/`
(source.mp4, audio.wav, frames/, renders/). `video_id` = short hash of source URL/path.

## 4. Agent tools (typed multimodal primitives)

| tool | returns | budget notes |
|---|---|---|
| `get_video_map()` | duration, chapter/scene tree with 1-liners, stats | cheap, cached |
| `search_transcript(query, limit)` | FTS hits with ±1 segment context, timestamps | per-hit cost |
| `read_transcript(t0, t1)` | word-timestamped span, hard cap per call | priced by span |
| `view_frames(timestamps \| span, n≤6)` | JPEG frames the model actually sees | most expensive |
| `probe_audio(t0, t1)` | loudness curve, silences, speech density | cheap |
| `mark_moment(t0, t1, reason, evidence)` | registers a candidate moment | free |
| `propose_edl(cuts)` | validated EDL or precise rejection reasons | free |

Frames reach the model either as MCP image content blocks or via file-path + built-in `Read`
(image-capable) — whichever proves more reliable in integration testing.

## 5. CLI verbs (MVP)

- `cutroom log <url|file>` — ingest + index; prints the video map.
- `cutroom highlights <video> [-n 3] [--vertical]` — agent finds top moments → rendered clips
  with burned captions + receipts.md.
- `cutroom ask <video> "question"` — answer with [mm:ss] citations.
- `cutroom chapters <video>` — YouTube-ready chapter list.
- `cutroom cut <video> "instruction"` — free-form instruction → EDL → render.

## 6. Testing

- Unit tests are offline: synthetic fixture = ffmpeg `testsrc2` color segments (detectable shot
  boundaries) + macOS `say` speech at known timestamps (Linux fallback: espeak-ng or
  silence+tone, transcript assertions relaxed).
- EDL/captions/db/search/map: pure-logic tests, no media needed where possible.
- `requires_claude`-marked e2e: full `log → highlights` on the fixture, asserts rendered mp4
  duration & receipts present.

## 7. Milestones

- **M0 (this session)**: full MVP — all five verbs working on a real video, tests green.
- **M1**: smart vertical crop (face/active-speaker tracking via lightweight CPU detector);
  silence-trim and filler-word removal as `cut` presets.
- **M2**: AgenticVBench Repurpose subset run + honest scorecard in README; budget ablation
  (map+tools vs full-transcript baseline) — the headline chart.
- **M3**: multi-video projects ("find this claim across my 10 lectures"), export to
  EDL/OTIO for NLE handoff (DaVinci/Premiere).
