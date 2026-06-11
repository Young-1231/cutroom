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
from cutroom.agent.prompts import EDITOR_SYSTEM
from cutroom.agent.tools import make_toolkit
from cutroom.db import Workspace
from cutroom.types import EDL, Moment, edl_from_dict

DEFAULT_MODEL = "claude-sonnet-4-6"

# The editor may ONLY touch the indexed video through cutroom's own tools, plus the
# image-capable Read for saved frames. Built-in Bash/Write/WebFetch/etc. are never
# allowed: the transcript it reads is attacker-controllable (ASR of an arbitrary
# video), so an allowlist is the boundary against indirect prompt injection.
_READ_ONLY_BUILTIN = "Read"
# Defense in depth: also name the dangerous built-ins explicitly so they are stripped
# from the model's context, not merely denied at call time.
_DISALLOWED_BUILTINS = [
    "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch",
    "Task", "Agent", "KillShell", "BashOutput",
]


def _system_prompt(output_language: str | None) -> str:
    if output_language is None:
        return EDITOR_SYSTEM
    return f"{EDITOR_SYSTEM}\n\nWrite all user-facing output in {output_language}."


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


async def run_editor(
    ws: Workspace,
    video_id: str,
    task_prompt: str,
    budget_chars: int = 120_000,
    model: str | None = None,
    max_turns: int = 40,
    output_language: str | None = None,
) -> EditorResult:
    ledger = Ledger(total_chars=budget_chars)
    registry: dict = {}
    kit = make_toolkit(ws, video_id, ledger, registry)
    allowed = {*kit["tool_names"], _READ_ONLY_BUILTIN}
    options = ClaudeAgentOptions(
        model=model or os.environ.get("CUTROOM_MODEL") or DEFAULT_MODEL,
        system_prompt=_system_prompt(output_language),
        mcp_servers={"cutroom": kit["server"]},
        allowed_tools=list(allowed),
        disallowed_tools=_DISALLOWED_BUILTINS,
        # "default" (not bypassPermissions) so anything outside the allowlist reaches
        # the permission gate below and is denied instead of silently auto-approved.
        permission_mode="default",
        can_use_tool=_make_permission_gate(allowed),
        max_turns=max_turns,
        # SDK isolation: never inherit the host user's settings or CLAUDE.md files —
        # without this the editor adopts whatever language/rules the host configured.
        setting_sources=[],
    )
    final_text = ""
    num_turns = 0
    ok = True
    error: str | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(task_prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                if texts:
                    final_text = "\n".join(texts)
            elif isinstance(msg, ResultMessage):
                num_turns = msg.num_turns
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
    return EditorResult(
        final_text=final_text,
        edl=edl_from_dict(edl_dict) if edl_dict else None,
        moments=list(registry.get("moments", [])),
        chars_used=ledger.spent,
        num_turns=num_turns,
        ok=ok,
        error=error,
    )


def run_editor_sync(
    ws: Workspace,
    video_id: str,
    task_prompt: str,
    budget_chars: int = 120_000,
    model: str | None = None,
    max_turns: int = 40,
    output_language: str | None = None,
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
        )
    )
