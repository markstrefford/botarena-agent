"""LLM backend abstraction — uniform interface for different providers.

Each backend implements:
    async complete(system: str, user: str) -> str

Supported backends:
    - anthropic: Claude models via Anthropic SDK
    - openai: OpenAI-compatible APIs (OpenAI, Together, Groq, etc.)
    - ollama: Local models via Ollama HTTP API
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod

import httpx


class Backend(ABC):
    """Base class for LLM backends."""

    @abstractmethod
    async def complete(self, system: str, user: str) -> str:
        """Send a prompt and return the model's text response."""

    @abstractmethod
    def describe(self) -> str:
        """Short description for logging (e.g. 'anthropic/claude-sonnet-4-20250514')."""


class AnthropicBackend(Backend):
    """Anthropic Claude backend via official SDK.

    Handles 429/529 (rate limit / overloaded) with exponential backoff.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        api_key: str | None = None,
        max_tokens: int = 1024,
    ):
        import anthropic

        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
        )

    async def complete(self, system: str, user: str) -> str:
        import anthropic

        max_retries = 3
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                message = await self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return message.content[0].text
            except anthropic.RateLimitError:
                if attempt == max_retries:
                    raise
                print(f"  [anthropic] Rate limited, retrying in {delay:.0f}s...")
                await asyncio.sleep(delay)
                delay *= 2
            except anthropic.APIStatusError as e:
                # 529 = overloaded
                if e.status_code == 529 and attempt < max_retries:
                    print(f"  [anthropic] API overloaded (529), retrying in {delay:.0f}s...")
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    raise
        raise RuntimeError("Unreachable")  # pragma: no cover

    def describe(self) -> str:
        return f"anthropic/{self.model}"


class OpenAICompatBackend(Backend):
    """OpenAI-compatible API backend (OpenAI, Together, Groq, etc.)."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        max_tokens: int = 1024,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self._client = httpx.AsyncClient(
            timeout=120.0,
            headers={
                "Authorization": f"Bearer {api_key or os.getenv('OPENAI_API_KEY', '')}",
                "Content-Type": "application/json",
            },
        )

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.max_tokens,
        }
        resp = await self._client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def describe(self) -> str:
        return f"openai-compat/{self.model}"


class OllamaBackend(Backend):
    """Ollama local model backend via HTTP API."""

    def __init__(
        self,
        model: str = "qwen3:8b",
        base_url: str = "http://localhost:11434",
        json_mode: bool = False,
        chat_params: dict | None = None,
        timeout: float = 600.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.json_mode = json_mode
        self.chat_params = chat_params or {}
        self._client = httpx.AsyncClient(timeout=timeout)

    async def complete(self, system: str, user: str) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if self.json_mode:
            payload["format"] = "json"
        # Merge extra chat params (e.g. {"think": false} for Qwen3)
        payload.update(self.chat_params)
        resp = await self._client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        text = msg.get("content", "")
        # Capture thinking from reasoning models (ministral, qwen3 with think:true)
        thinking = msg.get("thinking", "")
        if thinking:
            # Wrap in <think> tags so brain.py can extract it
            text = f"<think>{thinking}</think>\n{text}"
        # Replace unicode chars that break Windows console (charmap codec)
        return text.encode("ascii", errors="replace").decode("ascii")

    def describe(self) -> str:
        return f"ollama/{self.model}"


def create_backend(
    backend: str,
    model: str,
    base_url: str = "",
    api_key: str = "",
    json_mode: bool = False,
    chat_params: dict | None = None,
) -> Backend:
    """Factory for creating backends from config.

    Args:
        backend: "anthropic", "openai", or "ollama"
        model: Model name/ID
        base_url: Override API base URL (required for ollama/openai-compat)
        api_key: API key (for anthropic/openai)
    """
    if backend == "anthropic":
        return AnthropicBackend(
            model=model,
            api_key=api_key or None,
        )
    elif backend == "openai":
        return OpenAICompatBackend(
            model=model,
            base_url=base_url or "https://api.openai.com/v1",
            api_key=api_key or None,
        )
    elif backend == "ollama":
        return OllamaBackend(
            model=model,
            base_url=base_url or "http://localhost:11434",
            json_mode=json_mode,
            chat_params=chat_params,
        )
    else:
        raise ValueError(
            f"Unknown backend: {backend!r}. "
            "Use 'anthropic', 'openai', or 'ollama'."
        )
