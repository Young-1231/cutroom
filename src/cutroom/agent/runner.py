"""ClaudeSDKClient wiring: run one editing task against one indexed video."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import partial

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

from cutroom.agent.budget import Ledger
from cutroom.agent.prompts import EDITOR_SYSTEM
from cutroom.agent.tools import make_toolkit
from cutroom.db import Workspace
from cutroom.types import EDL, Moment, edl_from_dict

DEFAULT_MODEL = "claude-sonnet-4-6"


def _system_prompt(output_language: str | None) -> str:
    if output_language is None:
        return EDITOR_SYSTEM
    return f"{EDITOR_SYSTEM}\n\nWrite all user-facing output in {output_language}."


@dataclass
class EditorResult:
    final_text: str
    edl: EDL | None
    moments: list[Moment]
    chars_used: int
    num_turns: int


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
    options = ClaudeAgentOptions(
        model=model or os.environ.get("CUTROOM_MODEL") or DEFAULT_MODEL,
        system_prompt=_system_prompt(output_language),
        mcp_servers={"cutroom": kit["server"]},
        # "Read" gives the model a native image-capable fallback for saved frames.
        allowed_tools=[*kit["tool_names"], "Read"],
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        # SDK isolation: never inherit the host user's settings or CLAUDE.md files —
        # without this the editor adopts whatever language/rules the host configured.
        setting_sources=[],
    )
    final_text = ""
    num_turns = 0
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
    edl_dict = registry.get("edl")
    return EditorResult(
        final_text=final_text,
        edl=edl_from_dict(edl_dict) if edl_dict else None,
        moments=list(registry.get("moments", [])),
        chars_used=ledger.spent,
        num_turns=num_turns,
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
