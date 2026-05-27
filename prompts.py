"""Prompt templates for BotArena planet controllers.

Builds system and user prompts from simulation state.
LLM agents set exact prices per asset — no strategy names.
Output format is JSON for reliable parsing.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You control pricing for a planet in a space trading simulation.

## Your Goal

Set prices for each tradeable asset to maximise your planet's wealth while keeping stock healthy.

## How Profit Works

Autonomous traders choose where to go based on:
    profit = sell_price - buy_price - travel_cost

- Higher buy prices attract sellers (agents delivering goods TO you)
- Lower sell prices attract buyers (agents buying goods FROM you)
- Travel cost depends on distance — nearby planets are cheaper to reach
- Other hubs are your competitors for trader traffic
- Watch the "Since Last Decision" section to see if your price changes are working

## Treasury

Your planet has a real treasury — a credit balance that constrains purchases.
Income = population x productivity x consumption_health x health_multiplier (per tick).
If treasury hits zero, the planet cannot buy deliveries. Sellers still earn revenue.
Set buy prices you can actually sustain: sustainable_price <= income / expected_delivery_rate.

## Rules

- Floor price is 0.01. No maximum.
- Between your decisions, prices hold steady (no heuristic override).
- If you're unsure, keep prices close to current values.

## Response Format

Respond with ONLY this JSON (no markdown, no prose):
{"prices": {"asset1": N, "asset2": N, ...}, "reasoning": "1-2 sentences explaining why"}

The "reasoning" field is critical — it gets logged with your price updates for analysis.
"""


def build_user_prompt(
    tick: int,
    planet_state: dict,
    market: dict | None = None,
    prev_state: dict | None = None,
) -> str:
    """Build per-planet user prompt with economic context for direct pricing.

    Simplified from llm/prompts.py — the arena already provides stock_delta_ema
    in planet_state, so we don't need separate stock_deltas or neighbor_distances.
    """
    pid = planet_state.get("id", "unknown")
    role = planet_state.get("role", "unknown")
    stock = planet_state.get("stock", {})
    prices = planet_state.get("prices", {})
    production = planet_state.get("production", {})
    consumption = planet_state.get("consumption", {})
    wealth = planet_state.get("wealth", 0)
    strategy = planet_state.get("current_strategy")
    stock_deltas = planet_state.get("stock_delta_ema")

    population = planet_state.get("population", 0)
    productivity = planet_state.get("productivity_per_capita", 1.0)
    health_mult = planet_state.get("health_multiplier", 1.0)
    has_refinery = planet_state.get("has_refinery", False)
    refinery_capacity = planet_state.get("refinery_capacity", 0.0)
    refinery_efficiency = planet_state.get("refinery_efficiency", 0.95)
    refining_rate = planet_state.get("refining_rate", {})

    lines = [f"## Tick {tick} — {pid} [{role}]\n"]

    # Treasury
    lines.append(f"Treasury: {wealth:.0f}")
    if population > 0:
        est_income = population * productivity * health_mult * health_mult
        lines.append(f"Population: {population:,} (productivity: {productivity})")
        lines.append(f"Health: {health_mult:.2f}")
        lines.append(f"Est. income/tick: {est_income:.0f}")

        total_cons_cost = sum(
            consumption.get(a, 0) * prices.get(a, 0) for a in consumption
        )
        if total_cons_cost > 0:
            net = est_income - total_cons_cost
            lines.append(f"Est. consumption cost/tick: {total_cons_cost:.0f} (net: {net:+.0f})")

        if total_cons_cost > 0 and wealth < total_cons_cost * 5:
            ticks_left = wealth / total_cons_cost
            lines.append(f"WARNING: Treasury critically low -- ~{ticks_left:.0f} ticks of purchases remaining")
    lines.append("")

    # Since Last Decision deltas
    if prev_state:
        prev_tick = prev_state.get("tick", 0)
        ticks_elapsed = tick - prev_tick
        lines.append(f"### Since Last Decision ({ticks_elapsed} ticks ago)")
        prev_stock = prev_state.get("stock", {})
        for asset in sorted(stock):
            cur = stock.get(asset, 0)
            prev_val = prev_stock.get(asset, 0)
            delta = cur - prev_val
            if cur == 0 and prev_val == 0:
                lines.append(f"  {asset}: +0 (was 0) -- STILL ZERO")
            else:
                lines.append(f"  {asset}: {delta:+,.0f} (was {prev_val:,.0f})")
        prev_wealth = prev_state.get("wealth", 0)
        wealth_delta = wealth - prev_wealth
        lines.append(f"  Treasury: {wealth_delta:+,.0f} (was {prev_wealth:,.0f})")
        lines.append("")

    # Stock with consumption runway
    lines.append("### Stock")
    for asset in sorted(stock):
        amount = stock[asset]
        if stock_deltas and asset in stock_deltas:
            net_flow = stock_deltas[asset]
        else:
            cons = consumption.get(asset, 0)
            prod = production.get(asset, 0)
            net_flow = prod - cons

        cons_rate = consumption.get(asset, 0)
        parts = [f"{asset}: {amount:,.0f}"]

        if abs(net_flow) > 0.01:
            parts.append(f"net {net_flow:+.1f}/tick")

        if amount <= 0:
            # Stockout -- show what's demanding this asset
            if has_refinery and asset == "fuel_raw":
                parts.append(f"STOCKOUT -- refinery demand: {refinery_capacity:.0f}/tick")
            elif cons_rate > 0:
                parts.append(f"STOCKOUT -- consumption: {cons_rate:.1f}/tick")
            else:
                parts.append("STOCKOUT")
        elif net_flow < -0.01 and amount > 0:
            runway = amount / (-net_flow)
            parts.append(f"~{runway:.0f} ticks until stockout")
        elif cons_rate > 0.01:
            runway = amount / cons_rate
            parts.append(f"{runway:,.0f} ticks of consumption")

        lines.append(f"  {' | '.join(parts)}")

    # Refinery info
    if has_refinery:
        lines.append(f"\n### Refinery (fuel_raw -> fuel_refined)")
        lines.append(f"  Capacity: {refinery_capacity:.0f}/tick, efficiency: {refinery_efficiency:.0%}")
        raw_consumed = refining_rate.get("fuel_raw", 0)
        refined_produced = refining_rate.get("fuel_refined", 0)
        if raw_consumed > 0:
            lines.append(f"  Currently refining: {raw_consumed:.1f} fuel_raw -> {refined_produced:.1f} fuel_refined per tick")
        else:
            lines.append(f"  Currently idle (no fuel_raw in stock)")

    # Current prices
    lines.append("\n### Current Prices")
    for asset in sorted(prices):
        lines.append(f"  {asset}: {prices[asset]:.4f}")

    # Production/consumption
    if production:
        prod_parts = [f"{k}: {v:.1f}" for k, v in sorted(production.items()) if v > 0]
        if prod_parts:
            lines.append(f"\nProduces: {', '.join(prod_parts)}")
    if consumption:
        cons_parts = [f"{k}: {v:.1f}" for k, v in sorted(consumption.items()) if v > 0]
        if cons_parts:
            lines.append(f"Consumes: {', '.join(cons_parts)}")

    # Previous reasoning
    if strategy and strategy.get("reasoning"):
        lines.append(f"\nYour last reasoning: {strategy['reasoning']}")

    # Market overview (other hubs)
    if market:
        market_planets = market.get("planets", {})
        hub_lines = []
        for mpid in sorted(market_planets):
            if mpid == pid or not mpid.endswith("-hub"):
                continue
            data = market_planets[mpid]
            n_prices = data.get("prices", {})
            price_str = ", ".join(f"{k}: {v:.2f}" for k, v in sorted(n_prices.items()))
            hub_lines.append(f"  {mpid}: [{price_str}]")
        if hub_lines:
            lines.append("\n### Other Hubs")
            lines.extend(hub_lines)

    # Concrete example
    asset_names = sorted(prices.keys())
    example_prices = ", ".join(f'"{a}": {prices.get(a, 1.0):.2f}' for a in asset_names)
    lines.append(
        f'\nRespond with JSON: '
        f'{{"prices": {{{example_prices}}}, "reasoning": "your reasoning here"}}'
    )

    return "\n".join(lines)
