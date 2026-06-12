"""ClaudeSDKClient wiring: run one editing task against one indexed video."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolUseBlock,
)

from cutroom.agent.budget import Ledger
from cutroom.agent.hooks import DENIED_BUILTINS, make_lifecycle_hooks
from cutroom.agent.prompts import EDITOR_SYSTEM
from cutroom.agent.tools import make_toolkit
from cutroom.checkpoints import save_checkpoint
from cutroom.db import Workspace
from cutroom.sessions import load_state, record_session, save_state
from cutroom.types import EDL, Moment, edl_from_dict

DEFAULT_MODEL = "claude-sonnet-4-6"

# The editor may ONLY touch the indexed video through cutroom's own tools, plus the
# image-capable Read for saved frames. Built-in Bash/Write/WebFetch/etc. are never
# allowed: the transcript it reads is attacker-controllable (ASR of an arbitrary
# video), so an allowlist is the boundary against indirect prompt injection.
# DENIED_BUILTINS (hooks.py) strips the dangerous built-ins from the model's context
# AND denies them at the PreToolUse gate — three layers with the allowlist below.
_READ_ONLY_BUILTIN = "Read"


def _system_prompt(output_language: str | None, recipe_lines: str = "") -> str:
    prompt = EDITOR_SYSTEM
    if recipe_lines:
        # Progressive disclosure: only name+summary in context; the body costs a
        # load_recipe call, so unused expertise stays out of the window.
        prompt += (
            "\n\nRecipe expertise available (name: summary):\n" + recipe_lines
            + "\nIf the task clearly matches one recipe, call load_recipe(name) once"
            " before investigating; otherwise ignore them."
        )
    if output_language is not None:
        prompt += f"\n\nWrite all user-facing output in {output_language}."
    return prompt


def _make_permission_gate(allowed: set[str]):
    """Allowlist gate: deny every tool not explicitly permitted (no human to prompt)."""

    async def can_use_tool(
        tool_name: str, _input: dict[str, Any], _ctx: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name in allowed or tool_name.startswith("mcp__cutroom__"):
            return PermissionResultAllow()
        return PermissionResultDeny(
            message=f"{tool_name} is not available to the cutroom editor", interrupt=False
        )

    return can_use_tool


@dataclass
class EditorResult:
    final_text: str
    edl: EDL | None
    moments: list[Moment]
    chars_used: int
    num_turns: int
    ok: bool = True
    error: str | None = None  # set when the session ended abnormally (max_turns, API error)
    session_id: str = ""  # resume/fork handle (`cutroom sessions`)
    review: dict | None = None  # critic sessions: {"verdicts": [...], "summary": str}


def _excludes_for_role(role: str) -> tuple[str, ...]:
    """Per-role toolkit surface, enforced by dropping tools from the MCP server.

    - editor: everything except submit_review (that is the critic's verdict channel)
    - scout : marks moments only — no EDL assembly, no recipe layer, no reviewing
    - critic: investigates and judges — cannot mark, cut, or pull recipe guidance,
      so its review stays grounded in the footage, not in editing ambitions
    """
    if role == "scout":
        return ("propose_edl", "load_recipe", "submit_review")
    if role == "critic":
        return ("propose_edl", "mark_moment", "load_recipe")
    return ("submit_review",)


_STEER_WRAPPER = (
    "[USER STEERING — guidance injected mid-run]\n{text}\n"
    "Adjust course accordingly and continue the task. All discipline still applies:"
    " receipts for every cut, watch the budget, no full-transcript dumps."
)


class StdinSteering:
    """Mid-run steering: each line typed on stdin interrupts the live session, and the
    drive loop re-injects it as user guidance. The prompt string is the only channel —
    same contract as resume/fork, so the receipts state carries through untouched."""

    def __init__(self, client: Any, notify: Callable[[str], None]):
        self._client = client
        self._notify = notify
        self._pending: list[str] = []

    async def run(self) -> None:
        """Reader task: blocks on stdin lines until cancelled or EOF."""
        while True:
            line = await anyio.to_thread.run_sync(
                sys.stdin.readline, abandon_on_cancel=True
            )
            if not line:  # EOF — no steering possible anymore
                return
            text = line.strip()
            if not text:
                continue
            self._pending.append(text)
            self._notify("⏸ steering received — interrupting the editor…")
            try:
                await self._client.interrupt()
            except Exception:  # noqa: BLE001 — session may have just finished; the
                pass  # pending text still gets injected by the drive loop


    def pop(self) -> str | None:
        return self._pending.pop(0) if self._pending else None


def _progress_line(block: ToolUseBlock) -> str:
    """One compact, human-scannable line per tool call — what steering reacts to."""
    name = block.name.removeprefix("mcp__cutroom__")
    args = block.input or {}
    detail = ""
    if name == "view_frames":
        detail = " " + ",".join(f"{t:g}s" for t in args.get("timestamps", [])[:6])
    elif name == "search_transcript":
        detail = f" {args.get('query', '')!r}"
    elif name in ("read_transcript", "probe_audio"):
        detail = f" {args.get('t0', '?')}–{args.get('t1', '?')}s"
    elif name == "load_recipe":
        detail = f" {args.get('name', '')}"
    elif name in ("mark_moment", "propose_edl"):
        detail = f" [{args.get('t0', '')}-{args.get('t1', '')}]" if "t0" in args else ""
    return f"→ {name}{detail}"


async def _drive_session(
    client: Any,
    task_prompt: str,
    steering: StdinSteering | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Send the task, stream the response, and re-engage after each user interrupt.

    Returns {final_text, num_turns, session_id, ok, error}. Only the LAST round
    decides ok/error: an interrupted round is not a failure, it is a redirect.
    """
    out: dict[str, Any] = {"final_text": "", "num_turns": 0, "session_id": "",
                           "ok": True, "error": None}
    prompt = task_prompt
    while True:
        out["ok"], out["error"] = True, None
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                if texts:
                    out["final_text"] = "\n".join(texts)
                if on_progress:
                    for b in msg.content:
                        if isinstance(b, ToolUseBlock):
                            on_progress(_progress_line(b))
            elif isinstance(msg, ResultMessage):
                out["num_turns"] += msg.num_turns
                out["session_id"] = msg.session_id or out["session_id"]
                if msg.result:
                    out["final_text"] = msg.result
                # A non-"success" subtype (error_max_turns, error_during_execution, …)
                # or is_error means the run did not finish cleanly — surface it instead
                # of passing a half-finished answer off as complete.
                if msg.is_error or (msg.subtype and msg.subtype != "success"):
                    out["ok"] = False
                    out["error"] = msg.subtype or "error"
                    if getattr(msg, "api_error_status", None):
                        out["error"] += f" (api status {msg.api_error_status})"
        # Deliberate: guidance that lands just AFTER the final result still gets
        # injected — the session re-engages for one more round (multi-turn query
        # on a completed response is the SDK's normal conversation flow).
        steer_text = steering.pop() if steering else None
        if steer_text is None:
            return out
        if on_progress:
            on_progress(f"↪ steering the editor: {steer_text}")
        prompt = _STEER_WRAPPER.format(text=steer_text)


async def run_editor(
    ws: Workspace,
    video_id: str,
    task_prompt: str,
    budget_chars: int = 120_000,
    model: str | None = None,
    max_turns: int = 40,
    output_language: str | None = None,
    resume: str | None = None,
    fork: bool = False,
    role: str = "editor",
    steer: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> EditorResult:
    ledger = Ledger(total_chars=budget_chars)
    registry: dict = {}
    if resume:
        # Rehydrate earned evidence: the gate must keep honoring frames the agent
        # actually viewed in the parent session, not force it to re-view everything.
        registry["viewed_frames"] = load_state(ws, video_id, resume)
    kit = make_toolkit(ws, video_id, ledger, registry, exclude=_excludes_for_role(role))
    recipe_lines = ""
    if role == "editor":
        from cutroom.recipes import load_recipes, recipe_summary_lines

        recipe_lines = recipe_summary_lines(load_recipes(ws.home / "recipes", strict=False))
    allowed = {*kit["tool_names"], _READ_ONLY_BUILTIN}
    options = ClaudeAgentOptions(
        model=model or os.environ.get("CUTROOM_MODEL") or DEFAULT_MODEL,
        system_prompt=_system_prompt(output_language, recipe_lines),
        mcp_servers={"cutroom": kit["server"]},
        allowed_tools=list(allowed),
        disallowed_tools=DENIED_BUILTINS,
        # "default" (not bypassPermissions) so anything outside the allowlist reaches
        # the permission gate below and is denied instead of silently auto-approved.
        permission_mode="default",
        can_use_tool=_make_permission_gate(allowed),
        # Lifecycle gates + audit trail: budget/evidence enforcement at the harness
        # layer (handlers keep their own checks as defense in depth). Every accepted
        # EDL is checkpointed (shadow-VCS) straight from the PostToolUse hook.
        hooks=make_lifecycle_hooks(
            ledger, registry, ws.renders_dir(video_id) / "trail.jsonl",
            on_edl_accepted=lambda edl, session: save_checkpoint(
                ws, video_id, edl, "agent", session
            ),
            # File sandbox: Read (granted for re-viewing saved frames) is confined
            # to this video's media directory — see make_lifecycle_hooks.
            read_roots=[ws.media_dir(video_id)],
        ),
        max_turns=max_turns,
        # SDK isolation: never inherit the host user's settings or CLAUDE.md files —
        # without this the editor adopts whatever language/rules the host configured.
        setting_sources=[],
        # Conversation JSONL is keyed by a cwd-derived project dir; pin cwd to the
        # workspace home so session ids resolve no matter where cutroom was invoked.
        cwd=str(ws.home),
        resume=resume,
        fork_session=fork,
    )
    async with ClaudeSDKClient(options=options) as client:
        if steer:
            async with anyio.create_task_group() as tg:
                steering = StdinSteering(client, on_progress or (lambda _line: None))
                tg.start_soon(steering.run)
                out = await _drive_session(client, task_prompt, steering, on_progress)
                tg.cancel_scope.cancel()
        else:
            out = await _drive_session(client, task_prompt, None, on_progress)
    edl_dict = registry.get("edl")
    if out["session_id"]:
        save_state(ws, video_id, out["session_id"], list(registry.get("viewed_frames", [])))
        record_session(
            ws, video_id, session_id=out["session_id"], task=task_prompt,
            turns=out["num_turns"], spent=ledger.spent, ok=out["ok"],
            edl=edl_dict is not None,
            resumed_from=resume or "", forked=fork, role=role,
        )
    return EditorResult(
        final_text=out["final_text"],
        edl=edl_from_dict(edl_dict) if edl_dict else None,
        moments=list(registry.get("moments", [])),
        chars_used=ledger.spent,
        num_turns=out["num_turns"],
        ok=out["ok"],
        error=out["error"],
        session_id=out["session_id"],
        review=registry.get("review"),
    )


def run_editor_sync(
    ws: Workspace,
    video_id: str,
    task_prompt: str,
    budget_chars: int = 120_000,
    model: str | None = None,
    max_turns: int = 40,
    output_language: str | None = None,
    resume: str | None = None,
    fork: bool = False,
    role: str = "editor",
    steer: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> EditorResult:
    return anyio.run(
        partial(
            run_editor,
            ws,
            video_id,
            task_prompt,
            budget_chars=budget_chars,
            model=model,
            max_turns=max_turns,
            output_language=output_language,
            resume=resume,
            fork=fork,
            role=role,
            steer=steer,
            on_progress=on_progress,
        )
    )
