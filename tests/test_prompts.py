"""Tests for agent prompt building (prompts.py)."""

from agent.prompts import SYSTEM_PROMPT, build_user_prompt


PLANET_STATE = {
    "id": "sol-hub",
    "role": "hub",
    "stock": {"food": 500, "energy": 300, "minerals": 200, "water": 400},
    "prices": {"food": 10.0, "energy": 15.0, "minerals": 20.0, "water": 8.0},
    "production": {},
    "consumption": {"food": 5, "energy": 3, "minerals": 2, "water": 4},
    "wealth": 50000,
    "population": 1000,
    "productivity_per_capita": 2.0,
    "health_multiplier": 0.95,
    "current_strategy": None,
}


class TestSystemPrompt:
    def test_contains_json_format(self):
        assert '"prices"' in SYSTEM_PROMPT
        assert '"reasoning"' in SYSTEM_PROMPT

    def test_mentions_treasury(self):
        assert "treasury" in SYSTEM_PROMPT.lower()


class TestBuildUserPrompt:
    def test_basic_output(self):
        prompt = build_user_prompt(tick=10, planet_state=PLANET_STATE)
        assert "Tick 10" in prompt
        assert "sol-hub" in prompt
        assert "Treasury: 50000" in prompt

    def test_includes_stock(self):
        prompt = build_user_prompt(tick=10, planet_state=PLANET_STATE)
        assert "food:" in prompt
        assert "energy:" in prompt

    def test_includes_prices(self):
        prompt = build_user_prompt(tick=10, planet_state=PLANET_STATE)
        assert "10.0000" in prompt  # food price
        assert "15.0000" in prompt  # energy price

    def test_includes_json_example(self):
        prompt = build_user_prompt(tick=10, planet_state=PLANET_STATE)
        assert "Respond with JSON" in prompt

    def test_with_market(self):
        market = {
            "planets": {
                "sol-hub": {"prices": {"food": 10.0}},
                "alpha-hub": {"prices": {"food": 12.0, "energy": 18.0}},
            }
        }
        prompt = build_user_prompt(tick=10, planet_state=PLANET_STATE, market=market)
        assert "alpha-hub" in prompt
        assert "Other Hubs" in prompt

    def test_stock_delta_ema(self):
        state = {**PLANET_STATE, "stock_delta_ema": {"food": -2.5, "energy": 1.0}}
        prompt = build_user_prompt(tick=10, planet_state=state)
        assert "observed" in prompt
        assert "-2.5" in prompt

    def test_low_treasury_warning(self):
        state = {**PLANET_STATE, "wealth": 100}  # very low
        prompt = build_user_prompt(tick=10, planet_state=state)
        assert "WARNING" in prompt
        assert "critically low" in prompt

    def test_previous_reasoning(self):
        state = {**PLANET_STATE, "current_strategy": {"reasoning": "hold prices steady"}}
        prompt = build_user_prompt(tick=10, planet_state=state)
        assert "hold prices steady" in prompt
