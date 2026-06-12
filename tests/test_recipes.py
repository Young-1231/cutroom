"""Editing recipes: registry sanity + CLI wiring (offline)."""

from typer.testing import CliRunner

from cutroom.cli import app
from cutroom.recipes import RECIPES, get_recipe, recipe_names

runner = CliRunner()


def test_registry_is_consistent():
    assert RECIPES, "no recipes registered"
    for name, rec in RECIPES.items():
        assert rec.name == name
        assert rec.summary and rec.guidance
        assert rec.budget > 0
        assert rec.n >= 0


def test_task_prompt_selection_vs_cut():
    shorts = get_recipe("podcast-shorts")
    prompt = shorts.task_prompt()
    assert "Recipe focus" in prompt
    assert 'target="vertical"' in prompt  # highlight-style carries the target
    teaser = get_recipe("teaser")
    assert teaser.n == 0
    cut_prompt = teaser.task_prompt()
    assert "teaser" in cut_prompt.lower()


def test_task_prompt_respects_n_override():
    rec = get_recipe("quotes")
    assert "5 strongest" in rec.task_prompt()
    assert "2 strongest" in rec.task_prompt(n_override=2)


def test_recipes_command_lists_all():
    res = runner.invoke(app, ["recipes"])
    assert res.exit_code == 0
    for name in recipe_names():
        assert name in res.output


def test_unknown_recipe_is_friendly(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))
    res = runner.invoke(app, ["recipe", "nope", "somevideo"])
    assert res.exit_code == 1
    assert "unknown recipe" in res.output
    assert "podcast-shorts" in res.output  # lists the real ones


def test_recipe_wires_config_into_edit_task(monkeypatch, seeded_ws):
    """`recipe teaser` must drive _run_edit_task with the recipe's prompt + reel + budget."""
    monkeypatch.setenv("CUTROOM_HOME", str(seeded_ws.home))
    import cutroom.cli as cli

    captured = {}

    def fake_run_edit_task(ref, prompt, budget, model, reel=False, plan_only=False):
        captured.update(ref=ref, prompt=prompt, budget=budget, reel=reel, plan_only=plan_only)

    monkeypatch.setattr(cli, "_run_edit_task", fake_run_edit_task)
    res = runner.invoke(app, ["recipe", "teaser", "testvid000001", "--plan"])
    assert res.exit_code == 0, res.output
    assert captured["reel"] is True  # teaser is a reel recipe
    assert captured["plan_only"] is True
    assert captured["budget"] == get_recipe("teaser").budget
    assert "teaser" in captured["prompt"].lower()
