"""Editing recipes: named, reusable expert workflows on top of the editor agent.

A recipe is the cutroom analogue of a Claude Code skill — a markdown file whose
frontmatter (summary + output config) is cheap enough to list everywhere, and whose
body (the expert guidance) is loaded only when the recipe is actually invoked:

- explicitly, via `cutroom recipe <name> <video>`;
- by the model, via the load_recipe tool — the editor sees only `name: summary` lines
  in its system prompt and pulls the body on demand (progressive disclosure).

Built-ins ship in recipes/builtin/. Users drop their own .md files into
$CUTROOM_HOME/recipes/ — same format, discovered automatically, override built-ins
by filename.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from cutroom.agent.prompts import task_cut, task_highlights

FRONTMATTER_KEYS = {"summary", "vertical", "reel", "budget", "n"}


class RecipeError(ValueError):
    """A recipe file that cannot be parsed (bad frontmatter, missing body)."""


@dataclass(frozen=True)
class Recipe:
    name: str
    summary: str
    vertical: bool
    reel: bool
    budget: int
    n: int  # >0: select n self-contained highlights; 0: one free-form cut/reel
    guidance: str  # expert framing layered onto the base task (the file body)
    source: str = "builtin"  # "builtin" or the user file's path

    def task_prompt(self, n_override: int | None = None) -> str:
        if self.n > 0:
            n = n_override or self.n
            return f"{task_highlights(n, self.vertical)}\n\nRecipe focus: {self.guidance}"
        return task_cut(self.guidance, self.vertical)


def _parse_value(key: str, raw: str, where: str):
    if key in ("vertical", "reel"):
        if raw not in ("true", "false"):
            raise RecipeError(f"{where}: {key} must be true or false, got {raw!r}")
        return raw == "true"
    if key in ("budget", "n"):
        try:
            return int(raw.replace("_", ""))
        except ValueError:
            raise RecipeError(f"{where}: {key} must be an integer, got {raw!r}") from None
    return raw


def parse_recipe(text: str, name: str, source: str = "builtin") -> Recipe:
    """Parse one recipe file: `---` frontmatter (key: value) + markdown body."""
    where = f"recipe {name!r}"
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RecipeError(f"{where}: must start with a `---` frontmatter block")
    fields: dict = {"vertical": False, "reel": False, "budget": 120_000, "n": 0}
    body_start = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, raw = line.partition(":")
        key = key.strip()
        if not sep or key not in FRONTMATTER_KEYS:
            raise RecipeError(
                f"{where}: bad frontmatter line {line!r}"
                f" (keys: {', '.join(sorted(FRONTMATTER_KEYS))})"
            )
        fields[key] = _parse_value(key, raw.strip().strip("\"'"), where)
    if body_start is None:
        raise RecipeError(f"{where}: frontmatter never closed with `---`")
    guidance = "\n".join(lines[body_start:]).strip()
    if not fields.get("summary"):
        raise RecipeError(f"{where}: frontmatter needs a summary")
    if not guidance:
        raise RecipeError(f"{where}: body (the expert guidance) is empty")
    if fields["budget"] <= 0 or fields["n"] < 0:
        raise RecipeError(f"{where}: budget must be > 0 and n >= 0")
    return Recipe(name=name, guidance=guidance, source=source, **fields)


def load_recipes(user_dir: Path | None = None, strict: bool = True) -> dict[str, Recipe]:
    """Built-in recipes, then user .md files from user_dir (override built-ins by name).

    strict=False skips unparseable user files instead of raising — agent runs must
    not die because of one broken file in ~/.cutroom/recipes/.
    """
    recipes: dict[str, Recipe] = {}
    builtin = files("cutroom.recipes") / "builtin"
    for entry in sorted(builtin.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            name = entry.name[: -len(".md")]
            recipes[name] = parse_recipe(entry.read_text(encoding="utf-8"), name)
    if user_dir is not None and user_dir.is_dir():
        for f in sorted(user_dir.glob("*.md")):
            try:
                recipes[f.stem] = parse_recipe(
                    f.read_text(encoding="utf-8"), f.stem, source=str(f)
                )
            except (RecipeError, OSError):
                if strict:
                    raise
    return recipes


def get_recipe(name: str, user_dir: Path | None = None) -> Recipe | None:
    return load_recipes(user_dir, strict=False).get(name)


def recipe_names(user_dir: Path | None = None) -> list[str]:
    return list(load_recipes(user_dir, strict=False))


def recipe_summary_lines(recipes: dict[str, Recipe]) -> str:
    """The cheap layer of progressive disclosure: one `name: summary` line each."""
    return "\n".join(f"- {r.name}: {r.summary}" for r in recipes.values())
