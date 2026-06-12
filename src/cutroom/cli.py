"""cutroom CLI — log / list / map / ask / highlights / chapters / cut / render
/ sessions / checkpoints / restore."""

from __future__ import annotations

import functools
import json
import sys
from subprocess import CalledProcessError

import typer
from rich.console import Console
from rich.table import Table

from cutroom.checkpoints import save_checkpoint
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


def _session_args(ws, video_id: str, resume: str | None, fork: str | None):
    """Expand --resume/--fork id prefixes; returns (full_session_id, fork_flag)."""
    if resume and fork:
        err.print("--resume and --fork are mutually exclusive")
        raise typer.Exit(1)
    if not resume and not fork:
        return None, False
    from cutroom.sessions import resolve_session

    return resolve_session(ws, video_id, resume or fork), bool(fork)


def _progress(line: str) -> None:
    console.print(line, style="dim", markup=False, highlight=False)


def _run_edit_task(
    ref: str, task_prompt: str, budget: int, model: str | None,
    reel: bool = False, plan_only: bool = False,
    resume: str | None = None, fork: str | None = None, steer: bool = False,
    verify: bool = False,
) -> None:
    """Single-agent edit: run the editor, then apply its result (plan or render)."""
    from cutroom.agent.runner import run_editor_sync

    ws = _ws()
    meta = _resolve(ws, ref)
    sid, forked = _session_args(ws, meta.id, resume, fork)
    console.print(f"[dim]editing {meta.title or meta.id} — budget {budget:,} chars[/dim]")
    if steer:
        console.print("[dim]steering on — type guidance + Enter to redirect mid-run[/dim]")
    result = run_editor_sync(
        ws, meta.id, task_prompt, budget_chars=budget, model=model, resume=sid, fork=forked,
        steer=steer, on_progress=_progress,
    )
    if verify and result.edl is not None:
        result = _verify_and_revise(ws, meta, result, task_prompt, budget, model)
    _apply_result(ws, meta, result, reel=reel, plan_only=plan_only)


def _verify_and_revise(ws, meta, result, task_prompt: str, budget: int, model):
    """Self-critique round: a FRESH critic session judges the accepted EDL on the
    footage (it cannot cut, mark, or load recipes); flagged issues get exactly one
    revision round, resumed into the editor's own session so receipts carry over."""
    from cutroom.agent.prompts import task_revise, task_verify
    from cutroom.agent.runner import run_editor_sync

    review_budget = max(20_000, budget // 4)
    console.print(f"[dim]verify: fresh-eyes review — budget {review_budget:,} chars[/dim]")
    critic = run_editor_sync(
        ws, meta.id, task_verify(edl_to_dict(result.edl)["cuts"], task_prompt[:200]),
        budget_chars=review_budget, model=model, role="critic", on_progress=_progress,
    )
    if not critic.review:
        console.print("[yellow]verify: no structured verdicts came back —"
                      " keeping the EDL unreviewed[/yellow]")
        return result
    verdicts = critic.review["verdicts"]
    issues = [f"cut {v['cut']}: {v['issue']}" for v in verdicts if not v["ok"]]
    summary = critic.review.get("summary", "")
    if not issues:
        console.print(f"verify ✓ all {len(verdicts)} cuts confirmed — {summary}",
                      style="green", markup=False)
        return result
    console.print(f"verify ✗ {len(issues)} of {len(verdicts)} cuts flagged:",
                  style="yellow", markup=False)
    for issue in issues:
        console.print(f"  {issue}", style="yellow", markup=False)
    console.print("[dim]verify: one revision round in the editor's session…[/dim]")
    revised = run_editor_sync(
        ws, meta.id, task_revise(issues), budget_chars=review_budget, model=model,
        resume=result.session_id, on_progress=_progress,
    )
    if revised.edl is not None:
        return revised
    console.print("[yellow]revision produced no EDL — keeping the original[/yellow]")
    return result


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
    save_checkpoint(ws, meta.id, edl_to_dict(edl), "plan" if plan_only else "render")
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
        _say_session(result)
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
    _say_session(result)


def _say_session(result) -> None:
    sid = getattr(result, "session_id", "")
    if sid:
        console.print(
            f"[dim]session {sid[:8]} — iterate with --resume {sid[:8]},"
            f" branch with --fork {sid[:8]}[/dim]"
        )


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
def list_videos(
    ids: bool = typer.Option(False, "--ids", help="bare video ids, one per line (for scripts)"),
) -> None:
    """Logged videos."""
    from cutroom.index.map import fmt_ts

    ws = _ws()
    videos = ws.list_videos()
    if ids:
        for v in videos:
            print(v.id)
        return
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
    resume: str | None = typer.Option(
        None, "--resume", metavar="SESSION", help="continue a previous session (id prefix)"
    ),
    fork: str | None = typer.Option(
        None, "--fork", metavar="SESSION", help="branch a previous session into a new one"
    ),
    steer: bool = typer.Option(
        False, "--steer", help="type guidance + Enter to redirect the editor mid-run"
    ),
) -> None:
    """Answer a question about the video with [mm:ss] citations."""
    from cutroom.agent.prompts import task_ask
    from cutroom.agent.runner import run_editor_sync

    ws = _ws()
    meta = _resolve(ws, video)
    sid, forked = _session_args(ws, meta.id, resume, fork)
    if steer:
        console.print("[dim]steering on — type guidance + Enter to redirect mid-run[/dim]")
    result = run_editor_sync(
        ws, meta.id, task_ask(question), budget_chars=budget, model=model,
        resume=sid, fork=forked, steer=steer, on_progress=_progress,
    )
    _say(result.final_text)
    if not result.ok:
        err.print(f"answer may be incomplete — the editor stopped early ({result.error})")
    console.print(f"[dim]budget used: {result.chars_used:,} chars, {result.num_turns} turns[/dim]")
    _say_session(result)


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
    steer: bool = typer.Option(
        False, "--steer", help="type guidance + Enter to redirect the editor mid-run"
    ),
    verify: bool = typer.Option(
        False, "--verify", help="fresh-eyes critic reviews the EDL; one revision round"
    ),
) -> None:
    """Find and render the n best moments as clips with burned captions."""
    from cutroom.agent.prompts import task_highlights

    if fanout:
        if steer or verify:
            err.print("--steer/--verify work with a single editor, not --fanout scouts")
            raise typer.Exit(1)
        _run_fanout_task(video, n, vertical, budget, model, plan_only=plan)
    else:
        _run_edit_task(video, task_highlights(n, vertical), budget, model,
                       plan_only=plan, steer=steer, verify=verify)


@app.command("recipes")
@friendly
def list_recipes() -> None:
    """Editing recipes (named expert workflows) — built-in + your own .md files."""
    from cutroom.recipes import load_recipes

    user_dir = _ws().home / "recipes"
    table = Table("recipe", "format", "clips", "source", "what it makes")
    for r in load_recipes(user_dir).values():
        table.add_row(r.name, "9:16" if r.vertical else "16:9",
                      str(r.n) if r.n else "reel",
                      "builtin" if r.source == "builtin" else "user", r.summary)
    console.print(table)
    console.print(
        "\n[dim]run one with:[/dim]  cutroom recipe <name> <video> [--plan]"
        f"\n[dim]add your own:[/dim]  drop a .md file into {user_dir}"
        " (frontmatter: summary/vertical/reel/budget/n; body = the guidance)"
    )


@app.command()
@friendly
def recipe(
    name: str = typer.Argument(..., help="recipe name (see `cutroom recipes`)"),
    video: str = typer.Argument(..., help="video id (or prefix) or source substring"),
    n: int | None = typer.Option(None, "-n", help="override the number of clips"),
    plan: bool = typer.Option(False, "--plan", help="review the plan before rendering"),
    budget: int | None = typer.Option(None, help="override the recipe's char budget"),
    model: str | None = typer.Option(None),
    steer: bool = typer.Option(
        False, "--steer", help="type guidance + Enter to redirect the editor mid-run"
    ),
    verify: bool = typer.Option(
        False, "--verify", help="fresh-eyes critic reviews the EDL; one revision round"
    ),
) -> None:
    """Run a named editing recipe (e.g. `cutroom recipe podcast-shorts <video>`)."""
    from cutroom.recipes import get_recipe, recipe_names

    user_dir = _ws().home / "recipes"
    rec = get_recipe(name, user_dir)
    if rec is None:
        err.print(f"unknown recipe {name!r} — available: {', '.join(recipe_names(user_dir))}")
        raise typer.Exit(1)
    _run_edit_task(
        video, rec.task_prompt(n_override=n), budget or rec.budget, model,
        reel=rec.reel, plan_only=plan, steer=steer, verify=verify,
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
    # Checkpoint what is about to render — this is where human edits to edl.json
    # enter the undo history (dedupe makes it a no-op when nothing changed).
    save_checkpoint(ws, meta.id, edl_to_dict(edl), "render")
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
    resume: str | None = typer.Option(
        None, "--resume", metavar="SESSION", help="continue a previous session (id prefix)"
    ),
    fork: str | None = typer.Option(
        None, "--fork", metavar="SESSION",
        help="branch a previous session: try a different cut without re-paying investigation",
    ),
    steer: bool = typer.Option(
        False, "--steer", help="type guidance + Enter to redirect the editor mid-run"
    ),
    verify: bool = typer.Option(
        False, "--verify", help="fresh-eyes critic reviews the EDL; one revision round"
    ),
) -> None:
    """Free-form edit instruction → EDL → rendered clips (+ one concatenated reel)."""
    from cutroom.agent.prompts import task_cut

    _run_edit_task(video, task_cut(instruction, vertical), budget, model,
                   reel=True, plan_only=plan, resume=resume, fork=fork,
                   steer=steer, verify=verify)


@app.command()
@friendly
def trail(
    video: str,
    session: str | None = typer.Option(
        None, "--session", metavar="SESSION",
        help="full call-by-call timeline for one session (id prefix)",
    ),
    denials: bool = typer.Option(
        False, "--denials", help="only gate denials (sandbox, budget, evidence)"
    ),
) -> None:
    """Audit trail: every tool call, charge, gate denial, and session summary."""
    from cutroom.trail import group_sessions, read_trail, session_records

    ws = _ws()
    meta = _resolve(ws, video)
    records = read_trail(ws.renders_dir(meta.id) / "trail.jsonl")
    if not records:
        console.print("no trail yet — any agent run writes one")
        return
    if denials:
        denies = [r for r in records if r.get("event") == "deny"]
        if not denies:
            console.print("no denials recorded — every call passed the gates")
            return
        for r in denies:
            console.print(f"{r.get('ts', '')[:19]}  ✗ {r.get('tool', '')}"
                          f" [{r.get('session', '')[:8]}]  {r.get('reason', '')}",
                          markup=False, highlight=False)
        return
    if session is not None:
        for r in session_records(records, session):
            ts = r.get("ts", "")[11:19]
            ev = r.get("event")
            if ev == "tool":
                tool_name = str(r.get("tool", "")).removeprefix("mcp__cutroom__")
                line = (f"{ts}  {tool_name:<18} +{r.get('charged', 0):>6,}"
                        f"  remaining {r.get('remaining', 0):>8,}")
                if r.get("edl_accepted"):
                    line += f"  EDL accepted → {r.get('checkpoint', '')}"
                console.print(line, markup=False, highlight=False)
            elif ev == "deny":
                console.print(f"{ts}  ✗ deny {r.get('tool', '')} — {r.get('reason', '')}",
                              style="yellow", markup=False, highlight=False)
            elif ev == "tool_error":
                console.print(f"{ts}  ! error {r.get('tool', '')} — {r.get('error', '')}",
                              style="red", markup=False, highlight=False)
            elif ev == "stop":
                bd = ", ".join(f"{k} {v:,}" for k, v in (r.get("breakdown") or {}).items())
                console.print(
                    f"{ts}  ■ stop — spent {r.get('spent', 0):,}/{r.get('total', 0):,}"
                    f" ({bd}); {r.get('moments', 0)} moments,"
                    f" edl={'yes' if r.get('edl') else 'no'}",
                    markup=False, highlight=False)
        return
    table = Table("session", "started", "calls", "spent", "denied", "errors",
                  "moments", "edl")
    for st in group_sessions(records):
        table.add_row(
            st.session[:8], st.started[:19], str(st.calls), f"{st.spent:,}",
            str(st.denies) if st.denies else "", str(st.errors) if st.errors else "",
            str(st.moments), "yes" if st.edl else "",
        )
    console.print(table)
    console.print(
        f"[dim]drill in:  cutroom trail {meta.id} --session <id>"
        f"   ·   gate denials:  cutroom trail {meta.id} --denials[/dim]"
    )


@app.command()
@friendly
def sessions(video: str) -> None:
    """Editor sessions for a video — resume (--resume) or branch (--fork) any of them."""
    from cutroom.sessions import list_sessions

    ws = _ws()
    meta = _resolve(ws, video)
    recs = list_sessions(ws, meta.id)
    if not recs:
        console.print("no sessions yet — any ask/cut/highlights run creates one")
        return
    table = Table("session", "when", "task", "turns", "spent", "edl", "lineage")
    for r in recs:
        lineage = ""
        if r.get("resumed_from"):
            lineage = ("fork of " if r.get("forked") else "resumed ") + r["resumed_from"][:8]
        task = r.get("task", "")[:48]
        if r.get("role") == "scout":
            task = f"scout: {task}"[:48]
        table.add_row(
            r["session_id"][:8], r.get("ts", "")[:16], task,
            str(r.get("turns", "")), f"{r.get('spent', 0):,}",
            "yes" if r.get("edl") else "", lineage,
        )
    console.print(table)
    console.print(
        f"[dim]continue:  cutroom ask {meta.id} \"...\" --resume <session>"
        f"   ·   branch:  cutroom cut {meta.id} \"...\" --fork <session>[/dim]"
    )


@app.command()
@friendly
def checkpoints(
    video: str,
    diff: str | None = typer.Option(
        None, "--diff", metavar="CHECKPOINT",
        help="show what changed from CHECKPOINT to the current edl.json",
    ),
) -> None:
    """EDL undo history: every accepted/saved edit list, independent of any session."""
    from cutroom.checkpoints import diff_edls, list_checkpoints, load_checkpoint

    ws = _ws()
    meta = _resolve(ws, video)
    if diff is not None:
        edl_path = ws.renders_dir(meta.id) / "edl.json"
        if not edl_path.exists():
            err.print(f"no current EDL at {edl_path} to diff against")
            raise typer.Exit(1)
        old = load_checkpoint(ws, meta.id, diff)["edl"]
        current = json.loads(edl_path.read_text(encoding="utf-8"))
        lines = diff_edls(old, current)
        if not lines:
            console.print(f"current edl.json is identical to {diff}")
            return
        console.print(f"[bold]{diff} -> current edl.json[/bold]")
        for line in lines:
            _say(f"  {line}")
        return
    cps = list_checkpoints(ws, meta.id)
    if not cps:
        console.print("no checkpoints yet — they appear when an edit task lands an EDL")
        return
    table = Table("id", "when", "source", "cuts", "total")
    for cp in cps:
        table.add_row(cp.id, cp.ts, cp.source, str(cp.n_cuts), f"{cp.total_secs:.1f}s")
    console.print(table)
    console.print(f"[dim]restore with:  cutroom restore {meta.id} <id>[/dim]")


@app.command()
@friendly
def restore(
    video: str,
    checkpoint: str,
    scope: str = typer.Option(
        "edl", "--scope",
        help="what to restore: edl (the file) | session (the conversation) | both",
    ),
) -> None:
    """Restore a checkpoint — the EDL file, the agent session that made it, or both.

    EDL restores checkpoint the current state first, so they are undoable. Session
    restore re-opens the conversation that produced the checkpoint via resume/fork.
    """
    from cutroom.checkpoints import load_checkpoint, restore_checkpoint

    if scope not in ("edl", "session", "both"):
        err.print(f"--scope must be edl, session, or both (got {scope!r})")
        raise typer.Exit(1)
    ws = _ws()
    meta = _resolve(ws, video)
    rec = load_checkpoint(ws, meta.id, checkpoint)
    sid = ""
    if scope in ("session", "both"):
        from cutroom.sessions import resolve_session

        sid = rec.get("session", "")
        if not sid:
            err.print(
                f"{checkpoint} was saved from {rec.get('source', '?')!r}, not an agent"
                " session — there is no conversation to restore (try --scope edl)"
            )
            raise typer.Exit(1)
        sid = resolve_session(ws, meta.id, sid)  # verify it is a known session
    if scope in ("edl", "both"):
        pre_id, edl_path = restore_checkpoint(ws, meta.id, checkpoint)
        console.print(f"[bold green]restored[/bold green] {checkpoint} -> {edl_path}")
        if pre_id:
            console.print(
                f"previous state saved as [bold]{pre_id}[/bold] (restore is undoable)"
            )
        console.print(f"[dim]render it with:  cutroom render {meta.id}[/dim]")
    if sid:
        console.print(
            f"session [bold]{sid[:8]}[/bold] made this checkpoint — re-open it with:"
            f"\n  continue:  cutroom cut {meta.id} \"...\" --resume {sid[:8]}"
            f"\n  branch:    cutroom cut {meta.id} \"...\" --fork {sid[:8]}"
        )


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
