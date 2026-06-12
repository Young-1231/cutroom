"""Mid-run steering: drive-loop and stdin reader tested offline with a fake client."""

from __future__ import annotations

import io

import anyio
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from cutroom.agent.runner import StdinSteering, _drive_session, _progress_line


def assistant(*blocks):
    return AssistantMessage(content=list(blocks), model="m")


def result(text, turns=3, subtype="success", is_error=False):
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
        num_turns=turns, session_id="sess-1", result=text,
    )


class FakeClient:
    """Replays one scripted message list per query; records queries + interrupts."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.queries: list[str] = []
        self.interrupts = 0

    async def query(self, prompt):
        self.queries.append(prompt)

    def receive_response(self):
        msgs = self.rounds.pop(0)

        async def gen():
            for m in msgs:
                yield m

        return gen()

    async def interrupt(self):
        self.interrupts += 1


class FakeSteering:
    def __init__(self, texts):
        self._texts = list(texts)

    def pop(self):
        return self._texts.pop(0) if self._texts else None


def drive(client, steering=None, on_progress=None):
    return anyio.run(_drive_session, client, "do the task", steering, on_progress)


def test_drive_without_steering_single_round():
    client = FakeClient([[assistant(TextBlock("working")), result("the answer")]])
    out = drive(client)
    assert client.queries == ["do the task"]
    assert out["final_text"] == "the answer"
    assert out["num_turns"] == 3
    assert out["session_id"] == "sess-1"
    assert out["ok"] is True and out["error"] is None


def test_drive_reinjects_steering_and_clears_interrupt_error():
    # Round 1 ends interrupted (error subtype); the steer text must be re-queried and
    # the final verdict comes from round 2 alone.
    client = FakeClient([
        [result("partial", turns=4, subtype="error_during_execution", is_error=True)],
        [result("redirected answer", turns=2)],
    ])
    out = drive(client, steering=FakeSteering(["only the bert song"]))
    assert len(client.queries) == 2
    assert "USER STEERING" in client.queries[1]
    assert "only the bert song" in client.queries[1]
    assert out["final_text"] == "redirected answer"
    assert out["num_turns"] == 6  # accumulated across rounds
    assert out["ok"] is True and out["error"] is None


def test_drive_last_round_error_survives():
    client = FakeClient([
        [result("partial", subtype="error_during_execution", is_error=True)],
        [result("still bad", subtype="error_max_turns", is_error=True)],
    ])
    out = drive(client, steering=FakeSteering(["go on"]))
    assert out["ok"] is False and out["error"] == "error_max_turns"


def test_drive_reports_progress_lines():
    client = FakeClient([[
        assistant(ToolUseBlock(id="1", name="mcp__cutroom__view_frames",
                               input={"timestamps": [42.0, 46.5]})),
        result("done"),
    ]])
    lines = []
    drive(client, on_progress=lines.append)
    assert lines == ["→ view_frames 42s,46.5s"]


def test_progress_line_formats():
    cases = [
        ({"name": "mcp__cutroom__search_transcript", "input": {"query": "bomb"}},
         "→ search_transcript 'bomb'"),
        ({"name": "mcp__cutroom__read_transcript", "input": {"t0": 5, "t1": 20}},
         "→ read_transcript 5–20s"),
        ({"name": "mcp__cutroom__load_recipe", "input": {"name": "teaser"}},
         "→ load_recipe teaser"),
        ({"name": "mcp__cutroom__propose_edl", "input": {"cuts": []}},
         "→ propose_edl"),
        ({"name": "Read", "input": {"file_path": "/x"}}, "→ Read"),
    ]
    for kw, expected in cases:
        assert _progress_line(ToolUseBlock(id="1", **kw)) == expected


def test_stdin_steering_reads_interrupts_and_stops_on_eof(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  \nfocus on the song\n"))
    client = FakeClient([])
    notes = []
    steering = StdinSteering(client, notes.append)
    anyio.run(steering.run)  # returns at EOF
    assert steering.pop() == "focus on the song"  # blank line ignored
    assert steering.pop() is None
    assert client.interrupts == 1
    assert any("steering" in n for n in notes)
