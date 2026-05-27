"""Arena HTTP MCP client — lobby registration + tool calls + SSE events.

Adapted from arena/test_e2e.py. Async throughout.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

from .config import _ts


class ArenaClient:
    """Manages the full lifecycle: register → request run → play → scores."""

    def __init__(self, lobby_url: str) -> None:
        self.lobby_url = lobby_url.rstrip("/")
        self._lobby = httpx.AsyncClient(timeout=30.0)

        # Set after registration
        self.agent_id: str = ""
        self.api_key: str = ""

        # Set after run is ready
        self.arena_url: str = ""
        self.session_key: str = ""
        self._arena: httpx.AsyncClient | None = None

        # Shared state (updated by SSE listener)
        self.tick: int = 0
        self.sim_started = asyncio.Event()
        self.sim_complete = asyncio.Event()
        self.final_scores: dict | None = None

        # Pre-connect contract (ADR-004): set by listen_events when the
        # server's connection_ready preamble arrives. Callers must await
        # connection_ready before any tool call; every call_tool carries
        # connection_id in X-Connection-Id.
        self.connection_id: str | None = None
        self.connection_ready = asyncio.Event()

    # ── Lobby ────────────────────────────────────────────────────────

    def set_credentials(self, agent_id: str, api_key: str) -> None:
        """Set existing credentials (skip registration)."""
        self.agent_id = agent_id
        self.api_key = api_key

    async def register(self, name: str, email: str) -> tuple[str, str]:
        """Register a new agent. Returns (agent_id, api_key)."""
        resp = await self._lobby.post(
            f"{self.lobby_url}/arena/v1/register",
            json={"name": name, "description": "BotArena reference agent", "email": email},
        )
        resp.raise_for_status()
        data = resp.json()
        self.agent_id = data["agent_id"]
        self.api_key = data["api_key"]
        return self.agent_id, self.api_key

    async def request_run(
        self, test: bool = True, config: dict | None = None, max_retries: int = 5,
    ) -> str:
        """Request a new run. Returns run_id. Retries on 429 with backoff."""
        body: dict = {"test": test}
        if config:
            body["config"] = config

        backoff = 2.0
        for attempt in range(max_retries + 1):
            resp = await self._lobby.post(
                f"{self.lobby_url}/arena/v1/runs/request",
                headers={"Authorization": f"Bearer {self.agent_id}:{self.api_key}"},
                json=body,
            )
            if resp.status_code != 429 or attempt == max_retries:
                resp.raise_for_status()
                return resp.json()["run_id"]

            retry_after = resp.headers.get("retry-after")
            wait = float(retry_after) if retry_after else backoff
            print(f"  Rate limited — retrying in {wait:.0f}s (attempt {attempt + 1}/{max_retries})")
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, 60.0)

    async def wait_for_ready(self, run_id: str, timeout: float = 300) -> None:
        """Poll lobby until run is ready. Sets arena_url and session_key."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = await self._lobby.get(
                f"{self.lobby_url}/arena/v1/runs/{run_id}",
                headers={"Authorization": f"Bearer {self.agent_id}:{self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            status = data["status"]

            if status == "ready":
                self.arena_url = data["arena_url"]
                self.session_key = data["session_key"]
                self._arena = httpx.AsyncClient(
                    timeout=30.0,
                    headers={"Authorization": f"Bearer {self.session_key}"},
                )
                return
            elif status == "failed":
                raise RuntimeError(f"Run failed: {data.get('failure_reason', 'unknown')}")
            else:
                print(f"  {status}...", file=sys.stderr)
                await asyncio.sleep(5)

        raise TimeoutError(f"Run {run_id} not ready after {timeout}s")

    # ── MCP tool calls ───────────────────────────────────────────────

    async def call_tool(self, tool: str, args: dict | None = None) -> dict:
        """Call an MCP tool on the arena. Returns the result dict."""
        assert self._arena is not None, "Not connected to arena"
        if self.connection_id is None:
            raise RuntimeError(
                "Tool call attempted before SSE connection_ready preamble. "
                "Await client.connection_ready before calling tools (ADR-004)."
            )
        resp = await self._arena.post(
            f"{self.arena_url}/mcp/tools/call",
            json={"tool": tool, "args": args or {}},
            headers={"X-Connection-Id": self.connection_id},
        )
        resp.raise_for_status()
        return resp.json().get("result", {})

    async def get_simulation_info(self) -> dict:
        return await self.call_tool("get_simulation_info")

    async def get_planet_state(self, planet_id: str) -> dict:
        return await self.call_tool("get_planet_state", {"planet_id": planet_id})

    async def get_market_overview(self) -> dict:
        return await self.call_tool("get_market_overview")

    async def claim_planet(self, planet_id: str) -> dict:
        return await self.call_tool("claim_planet", {"planet_id": planet_id})

    async def set_strategy(
        self,
        planet_id: str,
        target_prices: dict[str, float],
        reasoning: str = "",
    ) -> dict:
        return await self.call_tool("set_strategy", {
            "planet_id": planet_id,
            "pricing_strategy": "fixed",
            "target_prices": target_prices,
            "reasoning": reasoning,
        })

    # ── SSE event listener ───────────────────────────────────────────

    async def listen_events(self) -> None:
        """Listen to SSE events until sim_complete. Run as background task."""
        assert self._arena is not None, "Not connected to arena"
        sse_timeout = httpx.Timeout(connect=30.0, read=1500.0, write=30.0, pool=30.0)
        sse_client = httpx.AsyncClient(
            timeout=sse_timeout,
            headers={"Authorization": f"Bearer {self.session_key}"},
        )
        try:
            async with sse_client.stream("GET", f"{self.arena_url}/mcp/events") as stream:
                async for line in stream.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    etype = event.get("type")

                    if etype == "connection_ready":
                        self.connection_id = event.get("connection_id")
                        self.connection_ready.set()

                    elif etype == "tick_update":
                        self.tick = event.get("tick", 0)

                    elif etype == "sim_started":
                        total = event.get("total_ticks", "?")
                        print(f"{_ts()} [arena] Simulation started ({total} ticks)", file=sys.stderr)
                        self.sim_started.set()

                    elif etype == "sim_complete":
                        self.final_scores = event.get("scores")
                        print(f"{_ts()} [arena] Simulation complete", file=sys.stderr)
                        self.sim_complete.set()
                        return
        finally:
            try:
                await asyncio.wait_for(sse_client.aclose(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass
