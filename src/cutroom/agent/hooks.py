"""Lifecycle hooks: code-enforced gates + an append-only audit trail for editor runs.

The tool handlers in cutroom.agent.tools validate their own inputs, but that is
per-handler discipline — a new or buggy tool could forget. These hooks move the three
session-wide invariants into the harness lifecycle, where no handler can skip them:

- PreToolUse  : deny side-effecting built-ins (3rd layer after disallowed_tools and
                can_use_tool), deny investigation tools once the budget is exhausted,
                and deny finalize calls that cite frames never actually viewed.
- PostToolUse : append every tool call to a per-video trail.jsonl with its budget
                charge — the persistent ledger, and the mount point for shadow-VCS
                checkpoints (commit-after-edit hangs off the edl_accepted record).
- Stop        : append the session summary (spend breakdown, moments, EDL state).

Gates only ever DENY or stay silent ({} = no opinion): allow/ask decisions remain the
permission gate's job, and precise argument errors remain the handlers' job — a gate
fires only when it can positively establish a violation.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import HookContext, HookMatcher

from cutroom.agent.budget import Ledger
from cutroom.agent.tools import EXHAUSTED_MSG, FRAME_TS_TOLERANCE

# Built-ins the editor must never run: the transcript it reads is attacker-controllable
# (ASR of an arbitrary video), so side-effecting / network tools are denied outright.
# (MultiEdit is gone from current CLIs — merged into Edit — and listing it makes the
# CLI warn about a dead deny rule; the allowlist gate still denies it on old CLIs.)
DENIED_BUILTINS = [
    "Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch",
    "Task", "Agent", "KillShell", "BashOutput",
]

_PREFIX = "mcp__cutroom__"
# Tools that spend budget investigating; denied at the gate once the ledger is empty.
# mark_moment / propose_edl stay allowed so an exhausted session can still finalize.
INVESTIGATION_TOOLS = frozenset(
    _PREFIX + n
    for n in ("get_video_map", "search_transcript", "read_transcript",
              "view_frames", "probe_audio", "load_recipe")
)
_MARK = _PREFIX + "mark_moment"
_PROPOSE = _PREFIX + "propose_edl"


def _append(trail_path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line; never let trail I/O break the editing session."""
    try:
        trail_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"ts": datetime.now(UTC).isoformat(timespec="seconds"), **record},
            ensure_ascii=False, separators=(",", ":"),
        )
        with trail_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _cited_frames(tool_name: str, tool_input: dict[str, Any]) -> list[float]:
    """Numeric frame timestamps a finalize call cites. Malformed entries are skipped —
    the handler owns precise argument errors; the gate only judges what it can parse."""
    raw: list[Any] = []
    if tool_name == _MARK:
        ts = tool_input.get("frame_ts")
        raw = ts if isinstance(ts, list) else []
    elif tool_name == _PROPOSE:
        cuts = tool_input.get("cuts")
        for c in cuts if isinstance(cuts, list) else []:
            ts = c.get("frame_ts") if isinstance(c, dict) else None
            raw += ts if isinstance(ts, list) else []
    out: list[float] = []
    for t in raw:
        try:
            out.append(float(t))
        except (TypeError, ValueError):
            continue
    return out


def make_lifecycle_hooks(
    ledger: Ledger,
    registry: dict,
    trail_path: Path,
    on_edl_accepted: Callable[[dict[str, Any], str], str | None] | None = None,
) -> dict[str, list[HookMatcher]]:
    """Hooks bound to one editor session's ledger + evidence registry + trail file.

    Several fan-out scouts may share one trail file; records carry the session id and
    each closure tracks its own ledger, so interleaved appends stay attributable.

    on_edl_accepted(edl_dict, session_id) fires from PostToolUse whenever propose_edl
    lands an EDL — the shadow-VCS checkpoint mount point; its return value (checkpoint
    id) is written into the trail record.
    """
    state = {"spent": 0}  # last seen ledger.spent, for per-call charge attribution

    def _deny_reason(tool: str, args: dict[str, Any]) -> str | None:
        if tool in DENIED_BUILTINS:
            return f"{tool} is not available to the cutroom editor"
        if tool in INVESTIGATION_TOOLS and ledger.exhausted:
            return EXHAUSTED_MSG
        unviewed = [
            t for t in _cited_frames(tool, args)
            if not any(abs(t - v) <= FRAME_TS_TOLERANCE
                       for v in registry.get("viewed_frames", []))
        ]
        if unviewed:
            return "evidence gate: " + "; ".join(
                f"frame {t:.2f}s was never viewed — call view_frames([{t:.2f}])"
                " before citing it" for t in unviewed
            )
        return None

    async def pre_tool_gate(
        payload: dict[str, Any], tool_use_id: str | None, _ctx: HookContext
    ) -> dict[str, Any]:
        tool = str(payload.get("tool_name", ""))
        reason = _deny_reason(tool, payload.get("tool_input") or {})
        if reason is None:
            return {}
        _append(trail_path, {
            "event": "deny", "tool": tool, "reason": reason,
            "session": payload.get("session_id", ""),
        })
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    async def post_tool_trail(
        payload: dict[str, Any], tool_use_id: str | None, _ctx: HookContext
    ) -> dict[str, Any]:
        # Charge delta is best-effort attribution: parallel tool calls in one turn
        # may interleave, but spent/remaining are always exact.
        charged = ledger.spent - state["spent"]
        state["spent"] = ledger.spent
        record: dict[str, Any] = {
            "event": "tool", "tool": str(payload.get("tool_name", "")),
            "tool_use_id": tool_use_id, "charged": charged,
            "spent": ledger.spent, "remaining": ledger.remaining,
            "session": payload.get("session_id", ""),
        }
        if record["tool"] == _PROPOSE and registry.get("edl") is not None:
            record["edl_accepted"] = True
            if on_edl_accepted is not None:
                try:
                    cp_id = on_edl_accepted(registry["edl"], record["session"])
                except Exception as e:  # noqa: BLE001 — checkpointing must never kill the session
                    record["checkpoint_error"] = f"{type(e).__name__}: {e}"[:200]
                else:
                    if cp_id:
                        record["checkpoint"] = cp_id
        _append(trail_path, record)
        return {}

    async def post_failure_trail(
        payload: dict[str, Any], tool_use_id: str | None, _ctx: HookContext
    ) -> dict[str, Any]:
        _append(trail_path, {
            "event": "tool_error", "tool": str(payload.get("tool_name", "")),
            "error": str(payload.get("error", ""))[:500],
            "session": payload.get("session_id", ""),
        })
        return {}

    async def stop_trail(
        payload: dict[str, Any], tool_use_id: str | None, _ctx: HookContext
    ) -> dict[str, Any]:
        _append(trail_path, {
            "event": "stop", "spent": ledger.spent, "total": ledger.total_chars,
            "breakdown": ledger.breakdown,
            "moments": len(registry.get("moments", [])),
            "edl": registry.get("edl") is not None,
            "session": payload.get("session_id", ""),
        })
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_gate])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_trail])],
        "PostToolUseFailure": [HookMatcher(hooks=[post_failure_trail])],
        "Stop": [HookMatcher(hooks=[stop_trail])],
    }
