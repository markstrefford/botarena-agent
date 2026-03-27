"""Tests for agent response parsing (brain.py)."""

import pytest

from agent.brain import _parse_prices, _try_parse_json


# ── _try_parse_json ──────────────────────────────────────────────────


class TestTryParseJson:
    def test_simple_object(self):
        assert _try_parse_json('{"a": 1}') == {"a": 1}

    def test_embedded_in_text(self):
        text = 'Here is the result: {"price": 10.5} hope that helps'
        assert _try_parse_json(text) == {"price": 10.5}

    def test_trailing_braces(self):
        text = '{"a": 1}} extra stuff'
        assert _try_parse_json(text) == {"a": 1}

    def test_no_json(self):
        assert _try_parse_json("no json here") is None

    def test_empty_string(self):
        assert _try_parse_json("") is None

    def test_nested_object(self):
        text = '{"prices": {"food": 1.5, "water": 2.0}, "reasoning": "test"}'
        result = _try_parse_json(text)
        assert result["prices"]["food"] == 1.5


# ── _parse_prices ────────────────────────────────────────────────────


ASSETS = ["energy", "food", "minerals", "water"]
CURRENT = {"energy": 15.0, "food": 10.0, "minerals": 20.0, "water": 8.0}


class TestParsePrices:
    def test_clean_json(self):
        raw = '{"prices": {"food": 12.0, "water": 9.0, "energy": 16.0, "minerals": 22.0}, "reasoning": "raise all"}'
        prices, reasoning = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 12.0
        assert prices["water"] == 9.0
        assert reasoning == "raise all"

    def test_flat_dict_fallback(self):
        """When response has no 'prices' key, treat as flat dict."""
        raw = '{"food": 11.0, "water": 7.0, "reasoning": "lower water"}'
        prices, reasoning = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 11.0
        assert prices["water"] == 7.0
        # energy/minerals should keep current values
        assert prices["energy"] == 15.0
        assert prices["minerals"] == 20.0

    def test_think_block_stripped(self):
        raw = '<think>Let me analyze the market...</think>{"prices": {"food": 5.0}, "reasoning": "cut food"}'
        prices, reasoning = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 5.0
        assert reasoning == "cut food"

    def test_markdown_code_fence(self):
        raw = '```json\n{"prices": {"food": 8.0}, "reasoning": "hold"}\n```'
        prices, reasoning = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 8.0

    def test_invalid_price_keeps_current(self):
        raw = '{"prices": {"food": -5.0, "water": "abc"}, "reasoning": "bad"}'
        prices, _ = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 10.0  # kept current (negative)
        assert prices["water"] == 8.0  # kept current (non-numeric)

    def test_floor_price(self):
        raw = '{"prices": {"food": 0.005}, "reasoning": "too low"}'
        prices, _ = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 10.0  # below 0.01 floor

    def test_unparseable_returns_current(self):
        prices, reasoning = _parse_prices("I don't know what to do", ASSETS, CURRENT)
        assert prices == CURRENT
        assert reasoning == ""

    def test_partial_prices(self):
        """Only some assets specified — others keep current."""
        raw = '{"prices": {"food": 12.0}, "reasoning": "just food"}'
        prices, _ = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 12.0
        assert prices["energy"] == 15.0
        assert prices["minerals"] == 20.0
        assert prices["water"] == 8.0

    def test_think_block_with_markdown(self):
        raw = (
            '<think>thinking hard</think>\n'
            '```json\n'
            '{"prices": {"food": 15.0, "energy": 20.0}, "reasoning": "raise both"}\n'
            '```'
        )
        prices, reasoning = _parse_prices(raw, ASSETS, CURRENT)
        assert prices["food"] == 15.0
        assert prices["energy"] == 20.0
        assert reasoning == "raise both"
