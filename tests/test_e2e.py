"""Live end-to-end: real ingest + a real Claude editor run on the synthetic video."""

import re
import shutil
import subprocess

import pytest

from cutroom.db import Workspace

pytestmark = [pytest.mark.slow, pytest.mark.requires_claude]


def _claude_available() -> bool:
    exe = shutil.which("claude")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "--version"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _claude_available(), reason="claude CLI not available")
def test_ask_cites_the_zebra(tmp_path, monkeypatch, synthetic_video):
    if not synthetic_video["has_speech"]:
        pytest.skip("no TTS available for the synthetic fixture")
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))

    from cutroom.agent.prompts import task_ask
    from cutroom.agent.runner import run_editor_sync
    from cutroom.ingest.logger import log_footage

    ws = Workspace()
    meta = log_footage(str(synthetic_video["path"]), ws, model_size="tiny")
    result = run_editor_sync(
        ws, meta.id, task_ask("What did the zebra do?"), budget_chars=40_000, max_turns=20
    )
    assert "zebra" in result.final_text.lower()
    assert re.search(r"\d{1,2}:\d{2}", result.final_text), result.final_text
    assert result.chars_used > 0
