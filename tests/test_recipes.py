"""Recipes as files: parsing, user-dir override, CLI wiring (offline)."""

import pytest
from typer.testing import CliRunner

from cutroom.cli import app
from cutroom.recipes import (
    RecipeError,
    get_recipe,
    load_recipes,
    parse_recipe,
    recipe_names,
    recipe_summary_lines,
)

runner = CliRunner()


def test_builtin_registry_is_consistent():
    recipes = load_recipes()
    assert recipes, "no built-in recipes found"
    for name, rec in recipes.items():
        assert rec.name == name
        assert rec.summary and rec.guidance
        assert rec.budget > 0
        assert rec.n >= 0
        assert rec.source == "builtin"


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


def test_parse_recipe_defaults():
    rec = parse_recipe("---\nsummary: a thing\n---\nDo the thing.", "my-recipe")
    assert (rec.vertical, rec.reel, rec.n) == (False, False, 0)
    assert rec.budget == 120_000
    assert rec.guidance == "Do the thing."


@pytest.mark.parametrize("text,fragment", [
    ("no frontmatter", "must start with"),
    ("---\nsummary: x\nbad line\n---\nbody", "bad frontmatter line"),
    ("---\nsummary: x\nvertical: maybe\n---\nbody", "true or false"),
    ("---\nsummary: x\nbudget: lots\n---\nbody", "integer"),
    ("---\nsummary: x\nnever closed", "never closed"),
    ("---\nvertical: true\n---\nbody", "summary"),
    ("---\nsummary: x\n---\n", "empty"),
    ("---\nsummary: a\nsummary: b\n---\nbody", "duplicate"),
])
def test_parse_recipe_friendly_errors(text, fragment):
    with pytest.raises(RecipeError, match=fragment):
        parse_recipe(text, "bad")


def test_user_dir_adds_and_overrides(tmp_path):
    (tmp_path / "my-style.md").write_text(
        "---\nsummary: my style\nvertical: true\nn: 2\n---\nMy guidance.",
        encoding="utf-8",
    )
    (tmp_path / "teaser.md").write_text(
        "---\nsummary: my teaser\n---\nLonger teasers.", encoding="utf-8"
    )
    recipes = load_recipes(tmp_path)
    assert recipes["my-style"].n == 2 and recipes["my-style"].vertical
    assert recipes["my-style"].source == str(tmp_path / "my-style.md")
    assert recipes["teaser"].summary == "my teaser"  # user overrides builtin by name


def test_broken_user_file_strict_vs_lenient(tmp_path):
    (tmp_path / "broken.md").write_text("not a recipe", encoding="utf-8")
    with pytest.raises(RecipeError):
        load_recipes(tmp_path)
    recipes = load_recipes(tmp_path, strict=False)
    assert "broken" not in recipes
    assert "teaser" in recipes  # built-ins survive


def test_summary_lines_are_compact():
    lines = recipe_summary_lines(load_recipes())
    assert "- teaser:" in lines
    assert "spoil" not in lines  # bodies stay out of the cheap layer


def test_recipes_command_lists_all(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))
    res = runner.invoke(app, ["recipes"])
    assert res.exit_code == 0
    for name in recipe_names():
        assert name in res.output


def test_recipes_command_shows_user_recipes(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTROOM_HOME", str(tmp_path))
    rdir = tmp_path / "recipes"
    rdir.mkdir(parents=True)
    (rdir / "my-style.md").write_text(
        "---\nsummary: my style\n---\nGuidance.", encoding="utf-8"
    )
    res = runner.invoke(app, ["recipes"])
    assert res.exit_code == 0
    assert "my-style" in res.output
    assert "user" in res.output


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

    def fake_run_edit_task(ref, prompt, budget, model, reel=False, plan_only=False, **kw):
        captured.update(ref=ref, prompt=prompt, budget=budget, reel=reel, plan_only=plan_only)

    monkeypatch.setattr(cli, "_run_edit_task", fake_run_edit_task)
    res = runner.invoke(app, ["recipe", "teaser", "testvid000001", "--plan"])
    assert res.exit_code == 0, res.output
    assert captured["reel"] is True  # teaser is a reel recipe
    assert captured["plan_only"] is True
    assert captured["budget"] == get_recipe("teaser").budget
    assert "teaser" in captured["prompt"].lower()
