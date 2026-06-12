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
    ref: str, task_prompt: str, budget: int, model: str | None,
    reel: bool = False, plan_only: bool = False,
) -> None:
    """Single-agent edit: run the editor, then apply its result (plan or render)."""
    from cutroom.agent.runner import run_editor_sync

    ws = _ws()
    meta = _resolve(ws, ref)
    console.print(f"[dim]editing {meta.title or meta.id} — budget {budget:,} chars[/dim]")
    result = run_editor_sync(ws, meta.id, task_prompt, budget_chars=budget, model=model)
    _apply_result(ws, meta, result, reel=reel, plan_only=plan_only)


def _run_fanout_task(
    ref: str, n: int, vertical: bool, budget: int, model: str | None, plan_only: bool = False,
) -> None:
    """Multi-agent highlights: scout windows in parallel, merge, then apply the result."""
    from cutroom.agent.fanout import highlights_fanout_sync

    ws = _ws()
    meta = _resolve(ws, ref)
    per_window = max(20_000, budget // 4)
    console.print(
        f"[dim]fan-out over {meta.title or meta.id} — {per_window:,} chars/window[/dim]"
    )
    result = highlights_fanout_sync(
        ws, meta.id, n, vertical, budget_per_window=per_window, model=model,
    )
    _apply_result(ws, meta, result, reel=False, plan_only=plan_only)


def _apply_result(ws, meta, result, reel: bool = False, plan_only: bool = False) -> None:
    """Shared tail: snap → validate → save edl.json → (print plan | render → receipts).

    With plan_only the plan is saved and printed but nothing is rendered — the human
    edits the EDL and runs `cutroom render` to apply it.
    """
    from cutroom.render.edl import snap_edl, validate_edl
    from cutroom.render.ffmpeg import render_edl, render_reel
    from cutroom.render.receipts import write_receipts

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
    if plan_only:
        _print_plan(ws, meta, edl, result)
        console.print(
            f"\n[bold]plan saved[/bold] → {edl_path}"
            f"\nedit the cuts there if you like, then render with:"
            f"\n  [cyan]cutroom render {meta.id}[/cyan]"
        )
        console.print(
            f"[dim]budget used: {result.chars_used:,} chars, {result.num_turns} turns[/dim]"
        )
        return
    outputs = render_edl(ws, edl)
    if reel and len(edl.cuts) > 1:
        outputs.append(render_reel(ws, edl))
    receipts = write_receipts(ws, edl, outputs, moments=result.moments)
    console.print(f"\n[bold green]{len(outputs)} clip(s) rendered[/bold green]")
    for p in outputs:
        console.print(f"  {p}")
    console.print(f"  receipts: {receipts}")
    console.print(f"[dim]budget used: {result.chars_used:,} chars, {result.num_turns} turns[/dim]")


def _print_plan(ws: Workspace, meta: VideoMeta, edl, result) -> None:
    """Human-readable edit plan: each cut's time range, reason, and cited transcript."""
    from cutroom.index.map import fmt_ts

    total = sum(c.t1 - c.t0 for c in edl.cuts)
    console.print(
        f"\n[bold]Edit plan[/bold] — {len(edl.cuts)} cut(s), {fmt_ts(total)} total,"
        f" target={edl.target}, captions={edl.captions}"
    )
    for i, cut in enumerate(edl.cuts, 1):
        head = f"  {i}. [{fmt_ts(cut.t0)}–{fmt_ts(cut.t1)}]"
        if cut.label:
            head += f"  {cut.label}"
        console.print(head)
        why = cut.evidence.note or _moment_reason_for(cut, result.moments)
        if why:
            _say(f"     why: {why}")
        segs = ws.get_segments_by_ids(cut.evidence.segment_ids)
        if segs:
            excerpt = " ".join(s.text.strip() for s in segs)
            _say(f"     “{excerpt[:160]}{'…' if len(excerpt) > 160 else ''}”")


def _moment_reason_for(cut, moments) -> str:
    for m in moments or []:
        if m.t0 < cut.t1 and m.t1 > cut.t0:
            return m.reason
    return ""


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
    plan: bool = typer.Option(False, "--plan", help="review the plan before rendering"),
    fanout: bool = typer.Option(
        False, "--fanout", help="scout the video in parallel windows (better for long video)"
    ),
    budget: int = typer.Option(120_000),
    model: str | None = typer.Option(None),
) -> None:
    """Find and render the n best moments as clips with burned captions."""
    from cutroom.agent.prompts import task_highlights

    if fanout:
        _run_fanout_task(video, n, vertical, budget, model, plan_only=plan)
    else:
        _run_edit_task(video, task_highlights(n, vertical), budget, model, plan_only=plan)


@app.command("recipes")
def list_recipes() -> None:
    """Built-in editing recipes (named expert workflows)."""
    from cutroom.recipes import RECIPES

    table = Table("recipe", "format", "clips", "what it makes")
    for r in RECIPES.values():
        table.add_row(r.name, "9:16" if r.vertical else "16:9",
                      str(r.n) if r.n else "reel", r.summary)
    console.print(table)
    console.print("\n[dim]run one with:[/dim]  cutroom recipe <name> <video> [--plan]")


@app.command()
@friendly
def recipe(
    name: str = typer.Argument(..., help="recipe name (see `cutroom recipes`)"),
    video: str = typer.Argument(..., help="video id (or prefix) or source substring"),
    n: int | None = typer.Option(None, "-n", help="override the number of clips"),
    plan: bool = typer.Option(False, "--plan", help="review the plan before rendering"),
    budget: int | None = typer.Option(None, help="override the recipe's char budget"),
    model: str | None = typer.Option(None),
) -> None:
    """Run a named editing recipe (e.g. `cutroom recipe podcast-shorts <video>`)."""
    from cutroom.recipes import get_recipe, recipe_names

    rec = get_recipe(name)
    if rec is None:
        err.print(f"unknown recipe {name!r} — available: {', '.join(recipe_names())}")
        raise typer.Exit(1)
    _run_edit_task(
        video, rec.task_prompt(n_override=n), budget or rec.budget, model,
        reel=rec.reel, plan_only=plan,
    )


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
    plan: bool = typer.Option(False, "--plan", help="review the plan before rendering"),
    budget: int = typer.Option(120_000),
    model: str | None = typer.Option(None),
) -> None:
    """Free-form edit instruction → EDL → rendered clips (+ one concatenated reel)."""
    from cutroom.agent.prompts import task_cut

    _run_edit_task(video, task_cut(instruction, vertical), budget, model, reel=True, plan_only=plan)


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
