"""cutroom CLI — log / list / map / ask / highlights / chapters / cut / render."""

from __future__ import annotations

import functools
import json
import sys
from subprocess import CalledProcessError

import typer
from rich.console import Console
from rich.table import Table

from cutroom.db import Workspace
from cutroom.types import VideoMeta, edl_to_dict

app = typer.Typer(
    name="cutroom",
    help="A film-editor agent that logs your footage before it cuts.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err = Console(stderr=True, style="bold red")

# Exceptions that mean "this operation can't proceed" rather than "cutroom has a bug":
# convert them to a one-line message instead of dumping a traceback at the user.
_EXPECTED_ERRORS = (
    CalledProcessError, FileNotFoundError, RuntimeError, ValueError, KeyError,
    json.JSONDecodeError, OSError,
)


def friendly(fn):
    """Wrap a command so expected failures print one line and exit 1, not a traceback."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except typer.Exit:
            raise
        except _EXPECTED_ERRORS as e:
            err.print(f"{type(e).__name__}: {e}".strip())
            raise typer.Exit(1) from e

    return wrapper


def _say(text: str) -> None:
    """Print agent- or transcript-derived text verbatim (no rich markup parsing, so
    citations like [seg 42] / [mm:ss] survive and stray brackets can't raise)."""
    console.print(text, markup=False, highlight=False)


def _ws() -> Workspace:
    return Workspace()


def _resolve(ws: Workspace, ref: str) -> VideoMeta:
    meta = ws.resolve_video(ref)
    if meta is None:
        err.print(f"no unique video matches {ref!r} — try `cutroom list`")
        raise typer.Exit(1)
    return meta


def _run_edit_task(
    ref: str, task_prompt: str, budget: int, model: str | None, reel: bool = False
) -> None:
    """Shared agent → snap → render → receipts flow for highlights/cut."""
    from cutroom.agent.runner import run_editor_sync
    from cutroom.render.edl import snap_edl, validate_edl
    from cutroom.render.ffmpeg import render_edl, render_reel
    from cutroom.render.receipts import write_receipts

    ws = _ws()
    meta = _resolve(ws, ref)
    console.print(f"[dim]editing {meta.title or meta.id} — budget {budget:,} chars[/dim]")
    result = run_editor_sync(ws, meta.id, task_prompt, budget_chars=budget, model=model)
    _say(result.final_text)
    if not result.ok:
        err.print(f"the editor stopped early ({result.error}) — nothing rendered")
        raise typer.Exit(2)
    if result.edl is None:
        err.print("the editor finished without proposing an EDL — nothing rendered")
        raise typer.Exit(2)
    edl = snap_edl(result.edl, ws.get_segments(meta.id), duration=meta.duration)
    # Snapping can shrink a minimum-length cut or shift an edge; surface anything the
    # validator still dislikes rather than letting ffmpeg discover it.
    problems = validate_edl(edl, meta.duration, require_evidence=False)
    if problems:
        console.print("[yellow]note: snapped EDL has soft issues — "
                      + "; ".join(problems) + "[/yellow]")
    edl_path = ws.renders_dir(meta.id) / "edl.json"
    edl_path.write_text(json.dumps(edl_to_dict(edl), indent=2), encoding="utf-8")
    outputs = render_edl(ws, edl)
    if reel and len(edl.cuts) > 1:
        outputs.append(render_reel(ws, edl))
    receipts = write_receipts(ws, edl, outputs, moments=result.moments)
    console.print(f"\n[bold green]{len(outputs)} clip(s) rendered[/bold green]")
    for p in outputs:
        console.print(f"  {p}")
    console.print(f"  receipts: {receipts}")
    console.print(f"[dim]budget used: {result.chars_used:,} chars, {result.num_turns} turns[/dim]")


@app.command()
@friendly
def log(
    source: str = typer.Argument(..., help="YouTube/URL or local video file"),
    summarize: bool = typer.Option(False, help="LLM one-liners for each scene (needs Claude)"),
    whisper_model: str | None = typer.Option(None, help="faster-whisper size (default: small)"),
) -> None:
    """Ingest + index a video ("log the footage"), then print its map."""
    from cutroom.index.map import render_video_map
    from cutroom.ingest.logger import log_footage

    ws = _ws()
    with console.status("logging footage…") as status:
        meta = log_footage(
            source, ws, model_size=whisper_model,
            on_step=lambda name: status.update(f"logging footage… [{name}]"),
        )
    if summarize:
        _summarize_scenes(ws, meta.id)
    _say(render_video_map(ws, meta.id))
    console.print(f"\n[bold green]logged[/bold green] {meta.id}  ({meta.title})")


@app.command("list")
def list_videos() -> None:
    """Logged videos."""
    from cutroom.index.map import fmt_ts

    ws = _ws()
    videos = ws.list_videos()
    if not videos:
        console.print("nothing logged yet — try `cutroom log <url-or-file>`")
        return
    table = Table("id", "title", "duration", "scenes")
    for v in videos:
        table.add_row(v.id, v.title or "—", fmt_ts(v.duration), str(len(ws.get_scenes(v.id))))
    console.print(table)


@app.command("map")
@friendly
def show_map(video: str = typer.Argument(..., help="video id (or prefix) or source substring")):
    """Print the hierarchical video map the agent works from."""
    from cutroom.index.map import render_video_map

    ws = _ws()
    meta = _resolve(ws, video)
    _say(render_video_map(ws, meta.id))


@app.command()
@friendly
def ask(
    video: str,
    question: str,
    budget: int = typer.Option(60_000, help="tool-result budget in chars"),
    model: str | None = typer.Option(None, help="override CUTROOM_MODEL"),
) -> None:
    """Answer a question about the video with [mm:ss] citations."""
    from cutroom.agent.prompts import task_ask
    from cutroom.agent.runner import run_editor_sync

    ws = _ws()
    meta = _resolve(ws, video)
    result = run_editor_sync(ws, meta.id, task_ask(question), budget_chars=budget, model=model)
    _say(result.final_text)
    if not result.ok:
        err.print(f"answer may be incomplete — the editor stopped early ({result.error})")
    console.print(f"[dim]budget used: {result.chars_used:,} chars, {result.num_turns} turns[/dim]")


@app.command()
@friendly
def chapters(
    video: str,
    budget: int = typer.Option(60_000),
    model: str | None = typer.Option(None),
) -> None:
    """YouTube-ready chapter markers."""
    from cutroom.agent.prompts import task_chapters
    from cutroom.agent.runner import run_editor_sync

    ws = _ws()
    meta = _resolve(ws, video)
    result = run_editor_sync(ws, meta.id, task_chapters(), budget_chars=budget, model=model)
    _say(result.final_text)
    if not result.ok:
        err.print(f"chapters may be incomplete — the editor stopped early ({result.error})")


@app.command()
@friendly
def highlights(
    video: str,
    n: int = typer.Option(3, "-n", help="number of clips"),
    vertical: bool = typer.Option(True, "--vertical/--landscape"),
    budget: int = typer.Option(120_000),
    model: str | None = typer.Option(None),
) -> None:
    """Find and render the n best moments as clips with burned captions."""
    from cutroom.agent.prompts import task_highlights

    _run_edit_task(video, task_highlights(n, vertical), budget, model)


@app.command()
@friendly
def render(
    video: str,
    target: str | None = typer.Option(
        None, help="override saved target: vertical | landscape"
    ),
    captions: bool | None = typer.Option(
        None, "--captions/--no-captions", help="override saved caption setting"
    ),
    basename: str = typer.Option("clip", help="output file prefix"),
) -> None:
    """Re-render the last saved EDL (renders/edl.json) without re-running the agent."""
    from cutroom.render.ffmpeg import render_edl
    from cutroom.render.receipts import write_receipts
    from cutroom.types import edl_from_dict

    ws = _ws()
    meta = _resolve(ws, video)
    edl_path = ws.renders_dir(meta.id) / "edl.json"
    if not edl_path.exists():
        err.print(f"no saved EDL at {edl_path} — run `cutroom highlights` or `cutroom cut` first")
        raise typer.Exit(1)
    try:
        edl = edl_from_dict(json.loads(edl_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        err.print(f"{edl_path} is not a valid EDL ({type(e).__name__}: {e})")
        raise typer.Exit(1) from e
    if target is not None:
        if target not in ("vertical", "landscape"):
            err.print("--target must be vertical or landscape")
            raise typer.Exit(1)
        edl.target = target
    if captions is not None:
        edl.captions = captions
    outputs = render_edl(ws, edl, basename=basename)
    receipts = write_receipts(ws, edl, outputs)
    console.print(f"[bold green]{len(outputs)} clip(s) re-rendered[/bold green]")
    for p in outputs:
        console.print(f"  {p}")
    console.print(f"  receipts: {receipts}")


@app.command()
@friendly
def cut(
    video: str,
    instruction: str = typer.Argument(..., help='e.g. "30s teaser focused on the demo"'),
    vertical: bool = typer.Option(False, "--vertical/--landscape"),
    budget: int = typer.Option(120_000),
    model: str | None = typer.Option(None),
) -> None:
    """Free-form edit instruction → EDL → rendered clips (+ one concatenated reel)."""
    from cutroom.agent.prompts import task_cut

    _run_edit_task(video, task_cut(instruction, vertical), budget, model, reel=True)


def _summarize_scenes(ws: Workspace, video_id: str) -> None:
    """One batched haiku call to replace heuristic scene summaries; best-effort."""
    scenes = ws.get_scenes(video_id)
    if not scenes:
        return
    numbered = []
    for i, sc in enumerate(scenes, 1):
        text = " ".join(s.text for s in ws.get_segments(video_id, sc.t0, sc.t1))[:600]
        numbered.append(f"{i}. {text or '(no speech)'}")
    prompt = (
        "For each numbered scene transcript below, write one factual <=12-word summary."
        " Reply with exactly one numbered line per scene, nothing else.\n\n"
        + "\n".join(numbered)
    )
    try:
        import anyio
        from claude_agent_sdk import ClaudeAgentOptions, query

        async def _go() -> str:
            out = ""
            opts = ClaudeAgentOptions(model="haiku", max_turns=1)
            async for msg in query(prompt=prompt, options=opts):
                if type(msg).__name__ == "ResultMessage" and msg.result:
                    out = msg.result
            return out

        reply = anyio.run(_go)
        updates = {}
        for line in reply.splitlines():
            line = line.strip()
            if "." in line and line.split(".", 1)[0].isdigit():
                idx, text = line.split(".", 1)
                updates[int(idx)] = text.strip()
        if updates:
            for i, sc in enumerate(scenes, 1):
                if i in updates:
                    sc.summary = updates[i][:140]
            ws.replace_scenes(video_id, scenes)
    except Exception as e:  # noqa: BLE001 — summaries are cosmetic, never fail the log
        err.print(f"scene summarizer unavailable ({e}); keeping heuristic summaries")


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
