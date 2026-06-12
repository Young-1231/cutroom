"""Editing recipes: named, reusable expert workflows on top of the editor agent.

A recipe is the cutroom analogue of a Claude Code skill — it packages "how an editor
approaches a common job" (the framing, the format, sensible defaults) behind one name,
so `cutroom recipe podcast-shorts <video>` replaces a paragraph of hand-written
instructions. Recipes compose the same evidence-gated editor; they only change the
prompt and the output config.
"""

from __future__ import annotations

from dataclasses import dataclass

from cutroom.agent.prompts import task_cut, task_highlights


@dataclass(frozen=True)
class Recipe:
    name: str
    summary: str
    vertical: bool
    reel: bool
    budget: int
    n: int  # >0: select n self-contained highlights; 0: one free-form cut/reel
    guidance: str  # expert framing layered onto the base task

    def task_prompt(self, n_override: int | None = None) -> str:
        if self.n > 0:
            n = n_override or self.n
            return f"{task_highlights(n, self.vertical)}\n\nRecipe focus: {self.guidance}"
        return task_cut(self.guidance, self.vertical)


RECIPES: dict[str, Recipe] = {
    r.name: r
    for r in [
        Recipe(
            name="podcast-shorts",
            summary="Vertical 9:16 shorts from a podcast/interview — hook + payoff, captioned",
            vertical=True, reel=False, budget=120_000, n=3,
            guidance=(
                "Each clip is a standalone short for TikTok/Reels: it must OPEN on a hook"
                " (a bold claim, a question, a surprising line) within the first ~2 seconds"
                " and land a clear payoff by the end. Prefer emotional peaks, strong opinions,"
                " and quotable lines over slow setup. Cut tight — no rambling intros."
            ),
        ),
        Recipe(
            name="talk-highlights",
            summary="Landscape highlights from a talk/lecture/keynote — the memorable beats",
            vertical=False, reel=False, budget=120_000, n=3,
            guidance=(
                "Pick the moments an attendee would clip and share: the sharpest claims, the"
                " demo reveals, the laugh lines, the one-sentence takeaways. Each must stand"
                " on its own without the surrounding context."
            ),
        ),
        Recipe(
            name="teaser",
            summary="One ~30s landscape teaser: open on a hook, end on a cliffhanger",
            vertical=False, reel=True, budget=90_000, n=0,
            guidance=(
                "Make a single ~30 second teaser of about 2-3 cuts: open on the most"
                " arresting moment in the whole video, build curiosity, and end right before"
                " a resolution so the viewer wants the full thing. Do not spoil the ending."
            ),
        ),
        Recipe(
            name="quotes",
            summary="Short vertical quote clips — the most quotable single sentences",
            vertical=True, reel=False, budget=100_000, n=5,
            guidance=(
                "Find the most quotable standalone sentences — the kind that work as a caption"
                " or a pull-quote. Keep each clip to roughly one sentence (about 5-15s),"
                " starting and ending exactly on the sentence boundary."
            ),
        ),
        Recipe(
            name="tighten",
            summary="A tightened landscape cut: drop dead air and rambling, keep the substance",
            vertical=False, reel=True, budget=140_000, n=0,
            guidance=(
                "Produce a tightened version of the video that keeps the substance but removes"
                " long silences, dead air, filler, and rambling tangents. Assemble the kept"
                " spans in their original order into one coherent cut."
            ),
        ),
    ]
}


def get_recipe(name: str) -> Recipe | None:
    return RECIPES.get(name)


def recipe_names() -> list[str]:
    return list(RECIPES)
