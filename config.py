"""Agent configuration — loads from .env with CLI overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from datetime import datetime

from dotenv import load_dotenv


def _ts() -> str:
    """Timestamp prefix for console output."""
    return datetime.now().strftime("%H:%M:%S")

# Load .env from agent/ directory
_AGENT_DIR = Path(__file__).resolve().parent
load_dotenv(_AGENT_DIR / ".env")

_VIEWER_BASE = os.environ.get("VIEWER_URL", "https://app.constellationproject.ai")


def viewer_url(arena_url: str) -> str:
    """Build viewer URL from arena container URL."""
    clean = arena_url.replace("https://", "").replace("http://", "")
    return f"{_VIEWER_BASE}/?arena={clean}"


@dataclass
class Settings:
    """All agent settings. Loaded from environment, overridable via CLI."""

    # BotArena credentials (leave empty to register on first run)
    agent_id: str = ""
    api_key: str = ""
    email: str = ""

    # LLM backend
    llm_backend: str = "anthropic"
    llm_model: str = "claude-sonnet-4-20250514"
    llm_api_key: str = ""
    llm_base_url: str = ""

    # Game settings
    target_planet: str = "sol-hub"
    decision_interval: int = 20
    lobby_url: str = "https://lobby.botarena.gg"
    test: bool = True
    decisions_file: str = ""
    chat_params: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from environment variables."""
        return cls(
            agent_id=os.getenv("BOTARENA_AGENT_ID", ""),
            api_key=os.getenv("BOTARENA_API_KEY", ""),
            email=os.getenv("BOTARENA_EMAIL", ""),
            llm_backend=os.getenv("LLM_BACKEND", "anthropic"),
            llm_model=os.getenv("LLM_MODEL", "claude-sonnet-4-20250514"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            target_planet=os.getenv("TARGET_PLANET", "sol-hub"),
            decision_interval=int(os.getenv("DECISION_INTERVAL", "20")),
            lobby_url=os.getenv("LOBBY_URL", "https://lobby.botarena.gg"),
            test=os.getenv("BOTARENA_TEST", "true").lower() in ("true", "1", "yes"),
            decisions_file=os.getenv("DECISIONS_FILE", ""),
        )

    def override(self, **kwargs) -> None:
        """Apply CLI overrides (skip None values)."""
        for key, value in kwargs.items():
            if value is not None and hasattr(self, key):
                setattr(self, key, value)
