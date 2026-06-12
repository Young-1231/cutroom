"""ClaudeSDKClient wiring: run one editing task against one indexed video."""

from __future__ import annotations

import os
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
) -> EditorResult:
    ledger = Ledger(total_chars=budget_chars)
    registry: dict = {}
    if resume:
        # Rehydrate earned evidence: the gate must keep honoring frames the agent
        # actually viewed in the parent session, not force it to re-view everything.
        registry["viewed_frames"] = load_state(ws, video_id, resume)
    # Scouts mark moments; only the orchestrator assembles EDLs. Dropping the tool
    # from the server makes that code-enforced, not prompt-trusted. Scouts also lose
    # load_recipe + the recipe list: their window-scout prompt is the whole job.
    kit = make_toolkit(ws, video_id, ledger, registry,
                       exclude=("propose_edl", "load_recipe") if role == "scout" else ())
    recipe_lines = ""
    if role != "scout":
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
    final_text = ""
    num_turns = 0
    ok = True
    error: str | None = None
    session_id = ""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task_prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                if texts:
                    final_text = "\n".join(texts)
            elif isinstance(msg, ResultMessage):
                num_turns = msg.num_turns
                session_id = msg.session_id or ""
                if msg.result:
                    final_text = msg.result
                # A non-"success" subtype (error_max_turns, error_during_execution, …)
                # or is_error means the run did not finish cleanly — surface it instead
                # of passing a half-finished answer off as complete.
                if msg.is_error or (msg.subtype and msg.subtype != "success"):
                    ok = False
                    error = msg.subtype or "error"
                    if getattr(msg, "api_error_status", None):
                        error += f" (api status {msg.api_error_status})"
    edl_dict = registry.get("edl")
    if session_id:
        save_state(ws, video_id, session_id, list(registry.get("viewed_frames", [])))
        record_session(
            ws, video_id, session_id=session_id, task=task_prompt,
            turns=num_turns, spent=ledger.spent, ok=ok, edl=edl_dict is not None,
            resumed_from=resume or "", forked=fork, role=role,
        )
    return EditorResult(
        final_text=final_text,
        edl=edl_from_dict(edl_dict) if edl_dict else None,
        moments=list(registry.get("moments", [])),
        chars_used=ledger.spent,
        num_turns=num_turns,
        ok=ok,
        error=error,
        session_id=session_id,
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
        )
    )
