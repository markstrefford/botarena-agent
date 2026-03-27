"""BotArena Reference Agent — entry point.

Usage:
    python -m agent
    python -m agent --backend anthropic --model claude-sonnet-4-20250514
    python -m agent --backend ollama --model qwen3:8b
    python -m agent --planet sol-hub --no-test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from .backends import create_backend
from .brain import Brain
from .client import ArenaClient
from .config import Settings, _ts


async def run(settings: Settings, run_config: dict | None = None) -> None:
    """Full lifecycle: register → request run → play → scores."""
    client = ArenaClient(settings.lobby_url)

    # ── Register or reuse credentials ────────────────────────────────
    if settings.agent_id and settings.api_key:
        client.set_credentials(settings.agent_id, settings.api_key)
        print(f"{_ts()} [agent] Using existing credentials: {settings.agent_id}")
    else:
        if not settings.email:
            print("Error: BOTARENA_EMAIL required for registration (set in .env)", file=sys.stderr)
            sys.exit(1)
        name = f"agent-{int(time.time())}"
        agent_id, api_key = await client.register(name, settings.email)
        print(f"{_ts()} [agent] Registered as {agent_id}")
        print(f"{_ts()} [agent] Save these to .env to skip registration next time:")
        print(f"  BOTARENA_AGENT_ID={agent_id}")
        print(f"  BOTARENA_API_KEY={api_key}")

    # ── Request run ──────────────────────────────────────────────────
    mode = "test" if settings.test else "LIVE"
    print(f"{_ts()} [agent] Requesting {mode} run...")
    run_id = await client.request_run(test=settings.test, config=run_config)
    print(f"{_ts()} [agent] Run {run_id} — waiting for arena...")

    await client.wait_for_ready(run_id)
    print(f"{_ts()} [agent] Arena ready: {client.arena_url}")
    print(f"{_ts()} [agent] Run ID: {run_id}")
    from .config import viewer_url
    print(f"{_ts()} [agent] Watch live: {viewer_url(client.arena_url)}")

    # ── Create LLM backend ───────────────────────────────────────────
    backend = create_backend(
        backend=settings.llm_backend,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        chat_params=settings.chat_params,
    )
    print(f"{_ts()} [agent] LLM: {backend.describe()}")

    decisions_file = Path(settings.decisions_file) if settings.decisions_file else None
    brain = Brain(
        backend=backend,
        target_planet=settings.target_planet,
        decision_interval=settings.decision_interval,
        decisions_file=decisions_file,
    )

    # ── Run SSE listener + brain concurrently ────────────────────────
    async with asyncio.TaskGroup() as tg:
        tg.create_task(client.listen_events())
        tg.create_task(brain.play(client))

    # ── Print scores ─────────────────────────────────────────────────
    if client.final_scores:
        print("\n=== Final Scores ===")
        for k, v in client.final_scores.items():
            print(f"  {k}: {v}")
    else:
        print("\n  No scores received")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BotArena Reference Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python -m agent                                    # Cloud (Anthropic)
  python -m agent --backend ollama --model qwen3:8b  # Local (Ollama)
  python -m agent --no-test                          # Live run (leaderboard)
""",
    )
    parser.add_argument("--backend", help="LLM backend: anthropic, openai, ollama")
    parser.add_argument("--model", help="Model name/ID")
    parser.add_argument("--api-key", help="LLM API key")
    parser.add_argument("--base-url", help="LLM API base URL")
    parser.add_argument("--planet", help="Target planet (default: sol-hub)")
    parser.add_argument("--interval", type=int, help="Ticks between decisions (default: 20)")
    parser.add_argument("--no-test", action="store_true", help="Live run (affects leaderboard)")
    parser.add_argument("--decisions-file", help="Path to write decisions JSON (for benchmarking)")
    parser.add_argument("--email", help="Email for registration")
    parser.add_argument("--ticks", type=int, help="Number of simulation ticks")
    parser.add_argument("--tick-speed", type=int, help="Tick speed in milliseconds")
    parser.add_argument("--chat-params", type=str, help="JSON string of extra Ollama chat params")

    args = parser.parse_args()

    # Load settings from .env, then apply CLI overrides
    settings = Settings.from_env()
    settings.override(
        llm_backend=args.backend,
        llm_model=args.model,
        llm_api_key=args.api_key,
        llm_base_url=args.base_url,
        target_planet=args.planet,
        decision_interval=args.interval,
        decisions_file=args.decisions_file,
        email=args.email,
    )
    if args.no_test:
        settings.test = False
    if args.chat_params:
        settings.chat_params = json.loads(args.chat_params)

    # Build per-run config overrides from CLI args
    run_config: dict | None = None
    if args.ticks or args.tick_speed:
        run_config = {}
        if args.ticks:
            run_config["ticks"] = args.ticks
        if args.tick_speed:
            run_config["tick_speed_ms"] = args.tick_speed

    asyncio.run(run(settings, run_config=run_config))


if __name__ == "__main__":
    main()
