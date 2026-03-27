"""Decision loop — observe, reason, act.

The Brain watches tick updates and periodically asks the LLM to set prices.
Response parsing (JSON extraction, <think> stripping) adapted from llm/runner.py.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import httpx

from .backends import Backend
from .client import ArenaClient
from .config import _ts
from .prompts import SYSTEM_PROMPT, build_user_prompt

# Regex to strip <think>...</think> blocks (Qwen3 reasoning models)
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _try_parse_json(text: str) -> dict | None:
    """Extract the first valid JSON object from text.

    Handles trailing braces by trying progressively shorter substrings.
    """
    start = text.find("{")
    if start == -1:
        return None
    pos = len(text)
    while pos > start:
        pos = text.rfind("}", start, pos)
        if pos == -1:
            return None
        try:
            data = json.loads(text[start : pos + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _parse_prices(
    raw_text: str,
    asset_names: list[str],
    current_prices: dict[str, float],
) -> tuple[dict[str, float], str]:
    """Parse LLM response into prices dict and reasoning string.

    Handles <think> blocks, markdown code fences, and JSON extraction.
    On parse failure, returns current prices with empty reasoning.
    """
    # 1. Strip <think>...</think> blocks
    cleaned = _THINK_RE.sub("", raw_text).strip()

    # 2. Strip markdown code fences
    if "```" in cleaned:
        parts = cleaned.split("```")
        if len(parts) >= 3:
            inner = parts[1]
            if inner.startswith("json"):
                inner = inner[4:]
            cleaned = inner.strip()

    # 3. Find JSON object
    data = _try_parse_json(cleaned)
    if data is None:
        return dict(current_prices), ""

    # 4. Extract reasoning
    reasoning = str(data.get("reasoning", ""))

    # 5. Extract and validate prices
    prices_data = data.get("prices", data)  # fallback: flat dict without "prices" key
    result = dict(current_prices)
    for asset in asset_names:
        val = prices_data.get(asset)
        if val is not None:
            try:
                price = float(val)
                if price >= 0.01:
                    result[asset] = price
            except (TypeError, ValueError):
                pass  # Keep current price for this asset

    return result, reasoning


class Brain:
    """Observe → reason → act loop for a single planet."""

    def __init__(
        self,
        backend: Backend,
        target_planet: str,
        decision_interval: int = 20,
        decisions_file: Path | None = None,
    ) -> None:
        self.backend = backend
        self.target_planet = target_planet
        self.decision_interval = decision_interval
        self.decisions_file = decisions_file
        self._decisions: list[dict] = []

    async def play(self, client: ArenaClient) -> None:
        """Main loop: claim planet, wait for sim, then decide every N ticks.

        Tool calls must happen BEFORE waiting for sim_started — the arena
        treats the first tool call as proof the agent is connected and won't
        start the simulation until it sees one.
        """
        # Immediately call a tool so the arena knows we're alive
        info = await client.get_simulation_info()
        planets = info.get("planets", [])
        planet_ids = [p["id"] for p in planets]
        print(f"{_ts()} [brain] Connected — {len(planet_ids)} planets available", file=sys.stderr)

        target = self.target_planet
        if target not in planet_ids:
            print(
                f"{_ts()} [brain] Target '{target}' not found, available: {planet_ids}",
                file=sys.stderr,
            )
            target = planet_ids[0] if planet_ids else None
            if not target:
                print(f"{_ts()} [brain] No planets available!", file=sys.stderr)
                return

        # Claim planet before sim starts
        result = await client.claim_planet(target)
        if result.get("error"):
            print(f"{_ts()} [brain] Failed to claim {target}: {result['error']}", file=sys.stderr)
        else:
            print(f"{_ts()} [brain] Claimed {target}", file=sys.stderr)

        # Now wait for the simulation to actually start
        print(f"{_ts()} [brain] Waiting for simulation to start...", file=sys.stderr)
        await client.sim_started.wait()

        # Decision loop
        last_decision_tick = -999
        try:
            while not client.sim_complete.is_set():
                await asyncio.sleep(1)

                if (client.tick - last_decision_tick) < self.decision_interval:
                    continue

                last_decision_tick = client.tick
                try:
                    await self._decide_and_act(client, target, client.tick)
                except (httpx.ConnectError, httpx.RemoteProtocolError):
                    # Arena container shut down (sim complete or timeout)
                    print(f"{_ts()} [brain] Arena disconnected, stopping", file=sys.stderr)
                    return
        finally:
            if self.decisions_file and self._decisions:
                self.decisions_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.decisions_file, "w", encoding="utf-8") as f:
                    json.dump(self._decisions, f, indent=2)
                print(f"{_ts()} [brain] Wrote {len(self._decisions)} decisions to {self.decisions_file}", file=sys.stderr)

    async def _decide_and_act(
        self,
        client: ArenaClient,
        planet_id: str,
        tick: int,
    ) -> None:
        """Single decision cycle: observe → LLM → set_strategy."""
        planet_state = await client.get_planet_state(planet_id)
        if "error" in planet_state:
            print(f"{_ts()}   T{tick} {planet_id}: error getting state", file=sys.stderr)
            return

        current_prices = planet_state.get("prices", {})
        asset_names = sorted(current_prices.keys())
        if not asset_names:
            return

        market = await client.get_market_overview()
        user_prompt = build_user_prompt(tick, planet_state, market)

        t0 = time.time()
        try:
            response_text = await self.backend.complete(SYSTEM_PROMPT, user_prompt)
            elapsed = time.time() - t0
        except Exception as e:
            elapsed = time.time() - t0
            print(f"{_ts()}   T{tick} {planet_id}: LLM error ({elapsed:.1f}s) {e}", file=sys.stderr)
            return

        target_prices, reasoning = _parse_prices(
            response_text, asset_names, current_prices,
        )

        if self.decisions_file is not None:
            think_match = _THINK_RE.search(response_text)
            thinking = think_match.group(1).strip() if think_match else ""
            stock = planet_state.get("stock", {})
            stock_deltas = planet_state.get("stock_delta_ema", {})
            for asset in asset_names:
                self._decisions.append({
                    "tick": tick,
                    "planet": planet_id,
                    "asset": asset,
                    "old_price": current_prices.get(asset, 0.0),
                    "new_price": target_prices.get(asset, 0.0),
                    "reasoning": reasoning,
                    "want": "",
                    "thinking": thinking,
                    "net_flow": stock_deltas.get(asset, 0.0),
                    "stock": stock.get(asset, 0.0),
                    "latency_s": elapsed,
                    "model": self.backend.describe(),
                    "system_prompt": SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "raw_response": response_text,
                })

        price_str = ", ".join(f"{k}: {v:.2f}" for k, v in sorted(target_prices.items()))
        print(f"{_ts()}   T{tick} {planet_id}: [{price_str}] ({elapsed:.1f}s) {reasoning[:80]}", flush=True)

        result = await client.set_strategy(
            planet_id=planet_id,
            target_prices=target_prices,
            reasoning=reasoning,
        )
        if not result.get("success"):
            print(
                f"{_ts()}   T{tick} {planet_id}: failed to set strategy: {result.get('error')}",
                file=sys.stderr,
            )
