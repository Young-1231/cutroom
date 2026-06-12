# Agent-harness paradigms (mid-2026) and what cutroom should adopt

Synthesis of a deep, adversarially-verified research pass (2026-06) over the current
generation of agent harnesses, mapped onto cutroom. Every mechanic below is
primary-sourced; recommendations are reasoned judgments on top of verified mechanics.

## The reusable building blocks (with evidence strength)

| Mechanism | What it is (verified mechanic) | Primary source | cutroom |
|---|---|---|---|
| **Hooks / lifecycle** | ~30 named events (PreToolUse/PostToolUse/Stop/SessionStart/UserPromptSubmit/SubagentStart-Stop/Pre-PostCompact/PermissionRequest). Declarative `event→matcher→handler` in settings.json; JSON in (stdin/POST) → decision out (`permissionDecision = allow\|deny\|ask\|defer`, precedence deny>defer>ask>allow). SDK = callbacks. | code.claude.com/docs/en/hooks, /agent-sdk/hooks | **missing** |
| **Checkpoint / time-travel** | Shadow Git repo separate from project VCS; commit project state after each edit tool; three restore granularities: Restore Files / Restore Task Only / Restore Files & Task. (untracked-file coverage uncertain) | docs.cline.bot/features/checkpoints | **missing** |
| **Session persistence** | Transcript as JSONL on local fs (`~/.claude/projects/<cwd>/*.jsonl`); `session_id` → resume(`options.resume`) or **fork**(`forkSession`) to branch alternatives. | /agent-sdk/session-storage, /sessions | **missing** |
| **Subagent isolation** | Agent tool; each subagent = fresh isolated context, only final message returns to parent, prompt string is the SOLE parent→child channel; restrict to read-only tools via `tools`. Known leak: claude-code #14118 (background subagents). | /agent-sdk/subagents | **partial** (have fan-out) |
| **Skills / commands-as-files** | SKILL.md + YAML frontmatter; dir name → /command; **progressive disclosure** (only ~1.5KB description in context, body loads on invoke); dual-mode invoke; `disable-model-invocation:true` for side-effecting workflows. | /docs/en/skills | **partial** (have recipes) |
| **Sandboxing** | bubblewrap (Linux) / seatbelt (macOS) fs+network isolation; ~84% fewer prompts (self-reported). **NOT a sufficient primary injection defense** — containment, not prevention. | anthropic.com/engineering/claude-code-sandboxing | **missing** |
| **MCP transports** | stdio / http-sse / in-process; `mcp__server__tool` naming; allowedTools permission. In-process is best for a self-contained tool. | /agent-sdk/mcp | **have** (in-process) |
| **Memory file** | AGENTS.md — Linux-Foundation/AAIF-stewarded, 60k+ projects (Codex/Cursor/Devin/Amp/Gemini CLI/Jules); plain markdown, parallels CLAUDE.md. | agents.md | **have** (CLAUDE.md) |
| **Scaffold taxonomy** | Scaffolds aren't discrete types — they compose 5 loop primitives (ReAct, generate-test-repair, plan-execute, multi-attempt retry, tree search); 4 tool categories recur (read/search/edit/execute). 11/13 compose multiple. | arXiv 2604.03515 (n=13, preprint) | validates cutroom |

The taxonomy independently **validates several cutroom choices**: index-first context ≈
Aider's PageRank repo-map; the four tool categories map to footage equivalents
(read clip/transcript · search index · edit EDL · execute render); and plan-execute +
multi-attempt retry (re-propose edits that fail evidence-gating) is the dominant
"compose multiple primitives" pattern.

## Ranked next adoptions for cutroom

1. **Hooks / lifecycle gates** (~2-4d) — highest leverage. Turns plan-mode + evidence-
   gating from *model-trusted* to *code-enforced*: a PreToolUse `deny/ask` gate on
   side-effecting tools (render/export/overwrite), PostToolUse handlers that write the
   budget ledger and enforce evidence mechanically. Also the mount point for #2 and ledger.
2. **Shadow-VCS checkpoint over the EDL/timeline** (~3-5d) — "undo to before that cut",
   independent of conversation history; three-way restore. Pairs with a PostToolUse hook
   that commits after each edit. Snapshot the EDL/state file, not the whole workspace.
3. **JSONL session persistence + resume/fork** (~2-3d) — fork is the killer feature for
   editing: branch a session to try a different cut style/pacing and compare.
4. **Harden fan-out sub-context isolation** (~1-2d) — read-only scout workers, evidence
   passed explicitly via prompt string, only final message returns. Refinement of existing
   fan-out; bounds parent-context cost + prevents worker writes.
5. **Bundle**: recipes → Skills progressive disclosure (~1-2d) · seatbelt fs-allowlist
   sandbox (~3-5d, partial security control) · AGENTS.md alongside CLAUDE.md (trivial).

## Honest gaps in this research

- **Source skew**: verified detail is Anthropic-heavy (Claude Code + SDK) + Cline + one
  taxonomy preprint. **Thin primary evidence on Codex CLI, Cursor, Aider internals, Roo,
  Devin, Amp, and OpenClaw** — the map is reliable for the Anthropic ecosystem, weak for
  competitors.
- **Mechanisms with NO surviving verified claim** (no standardized mechanic to copy —
  must be designed): mid-run steering / interruption, verification / self-critique / judge
  loops, observability / tracing / cost-accounting, streaming UX / output styles,
  background/async tasks, context-compaction specifics, model routing/fallback. Several of
  these (steering, verification, observability) are real cutroom gaps; they just lack a
  copyable industry-standard mechanic.
- **Refuted (do not rely on)**: sandbox as a *primary* injection defense (0-3); Cline
  checkpoints reliably capturing untracked files (1-2).
- **Weak metrics**: 84% prompt reduction is self-reported/unaudited; taxonomy is n=13
  preprint; the 1,536-char Skills figure was a 2-1 split.
