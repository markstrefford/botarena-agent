"""Tests for agent configuration (config.py)."""

from agent.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings()
        assert s.llm_backend == "anthropic"
        assert s.llm_model == "claude-sonnet-4-20250514"
        assert s.target_planet == "sol-hub"
        assert s.decision_interval == 20
        assert s.test is True

    def test_override(self):
        s = Settings()
        s.override(llm_backend="ollama", llm_model="qwen3:8b", target_planet="alpha-hub")
        assert s.llm_backend == "ollama"
        assert s.llm_model == "qwen3:8b"
        assert s.target_planet == "alpha-hub"

    def test_override_skips_none(self):
        s = Settings()
        s.override(llm_backend=None, llm_model="custom-model")
        assert s.llm_backend == "anthropic"  # unchanged
        assert s.llm_model == "custom-model"

    def test_override_skips_unknown_keys(self):
        s = Settings()
        s.override(nonexistent_key="value")  # should not raise
        assert not hasattr(s, "nonexistent_key")
