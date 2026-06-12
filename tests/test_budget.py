from cutroom.agent.budget import Ledger


def test_charge_arithmetic_and_breakdown():
    led = Ledger(total_chars=1000)
    assert led.remaining == 1000
    assert not led.exhausted
    led.charge("search", 300)
    led.charge("search", 100)
    led.charge("read", 50)
    assert led.spent == 450
    assert led.remaining == 550
    assert led.breakdown == {"search": 400, "read": 50}


def test_exhaustion_floors_remaining_at_zero():
    led = Ledger(total_chars=100)
    led.charge("read", 250)
    assert led.exhausted
    assert led.remaining == 0
    assert led.spent == 250
    assert led.line() == "[budget: 0/100 chars left]"


def test_exact_spend_is_exhausted():
    led = Ledger(total_chars=100)
    led.charge("map", 100)
    assert led.exhausted
    assert led.remaining == 0


def test_line_format_with_thousands_separators():
    led = Ledger()
    led.charge("map", 32_600)
    assert led.line() == "[budget: 87,400/120,000 chars left]"


def test_frame_cost_constant():
    assert Ledger.FRAME_COST == 1500


def test_runner_system_prompt_language():
    from cutroom.agent.prompts import EDITOR_SYSTEM
    from cutroom.agent.runner import _system_prompt

    assert _system_prompt(None) == EDITOR_SYSTEM
    assert _system_prompt("English").endswith("Write all user-facing output in English.")


def test_runner_system_prompt_recipe_disclosure():
    from cutroom.agent.runner import _system_prompt

    with_recipes = _system_prompt(None, "- teaser: one ~30s teaser")
    assert "- teaser: one ~30s teaser" in with_recipes
    assert "load_recipe" in with_recipes
