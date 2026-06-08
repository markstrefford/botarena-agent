"""Tests for the MCP pre-connect contract.

Per ADR-004 and story `mcp-pre-connect-contract`:

- The client must open the SSE event stream and observe the server's
  `connection_ready` preamble before issuing any tool call.
- The client must carry the server-issued `connection_id` in the
  `X-Connection-Id` header on every tool call.

Pure-asyncio tests for the wait primitive; httpx.MockTransport for the
header assertion.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent.client import ArenaClient


# ── Connection-ready primitive ────────────────────────────────────────


def test_connection_ready_event_exists():
    """ArenaClient exposes an asyncio.Event the caller can wait on."""
    client = ArenaClient("http://lobby.test")
    assert isinstance(client.connection_ready, asyncio.Event)
    assert not client.connection_ready.is_set()
    assert client.connection_id is None


def test_listen_events_sets_connection_ready_on_preamble():
    """When listen_events sees the connection_ready preamble, it stores
    the connection_id and sets the event."""

    async def run():
        client = ArenaClient("http://lobby.test")
        # The SSE stream returns the preamble then sim_complete so the
        # listener exits cleanly.
        lines = [
            'data: {"type": "connection_ready", "connection_id": "conn-abc"}',
            'data: {"type": "sim_complete", "scores": {"agent": 0}}',
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/mcp/events"
            body = "\n".join(lines) + "\n"
            return httpx.Response(200, text=body)

        client.arena_url = "http://arena.test"
        client.session_key = "sk-test"
        client._arena = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": f"Bearer {client.session_key}"},
        )

        # Patch the SSE client construction inside listen_events by
        # substituting a stream-capable mock client of our own. Easiest
        # route: monkey-patch httpx.AsyncClient to point at the same
        # MockTransport. The production code constructs its own client
        # for the SSE stream, so we patch via attribute override.
        # Override listen_events' httpx.AsyncClient through a shim.
        from agent import client as client_mod

        original = client_mod.httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs.pop("timeout", None)
            return original(transport=httpx.MockTransport(handler), **kwargs)

        client_mod.httpx.AsyncClient = factory
        try:
            await asyncio.wait_for(client.listen_events(), timeout=2.0)
        finally:
            client_mod.httpx.AsyncClient = original

        assert client.connection_ready.is_set()
        assert client.connection_id == "conn-abc"

    asyncio.run(run())


# ── Header propagation ────────────────────────────────────────────────


def test_call_tool_sends_x_connection_id_header():
    """call_tool sends the stored connection_id in the X-Connection-Id
    header on every tool call.

    Without this assertion, the server's single-session fallback would
    silently mask a missing-header bug — see ADR-004 consequences (b).
    """

    async def run():
        seen_headers: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_headers.append(dict(request.headers))
            return httpx.Response(200, json={"result": {"ok": True}})

        client = ArenaClient("http://lobby.test")
        client.arena_url = "http://arena.test"
        client.session_key = "sk-test"
        client._arena = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": f"Bearer {client.session_key}"},
        )
        client.connection_id = "conn-xyz"
        client.connection_ready.set()

        await client.call_tool("get_simulation_info")
        await client.call_tool("claim_planet", {"planet_id": "sol-hub"})

        assert len(seen_headers) == 2
        for h in seen_headers:
            assert h.get("x-connection-id") == "conn-xyz", (
                f"X-Connection-Id missing or wrong: {h}"
            )

    asyncio.run(run())


def test_call_tool_refuses_before_preamble():
    """Calling a tool before the connection_ready preamble is a contract
    violation. The client must refuse rather than silently land on the
    server's global fallback."""

    async def run():
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={"result": {}})

        client = ArenaClient("http://lobby.test")
        client.arena_url = "http://arena.test"
        client.session_key = "sk-test"
        client._arena = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": f"Bearer {client.session_key}"},
        )
        # connection_id NOT set, connection_ready NOT set.

        with pytest.raises(RuntimeError, match="connection_ready"):
            await client.call_tool("get_simulation_info")

        assert called is False, "tool call leaked through to the network"

    asyncio.run(run())


# ── Integration: brain.play must wait for the preamble ────────────────


def test_no_tool_calls_before_preamble_in_brain_play():
    """End-to-end-ish: the Brain's play() coroutine must not issue any
    tool call before the connection_ready preamble lands.

    Drives an ArenaClient with a controlled connection_ready event, and
    a stub _arena that records the call ordering."""

    async def run():
        from agent.brain import Brain

        events_seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            events_seen.append(body["tool"])
            # Minimal happy-path tool responses
            tool = body["tool"]
            if tool == "get_simulation_info":
                return httpx.Response(200, json={"result": {"planets": [{"id": "sol-hub"}]}})
            if tool == "claim_planet":
                return httpx.Response(200, json={"result": {"ok": True}})
            if tool == "get_planet_state":
                return httpx.Response(200, json={"result": {"prices": {}, "stock": {}}})
            if tool == "get_market_overview":
                return httpx.Response(200, json={"result": {}})
            if tool == "set_strategy":
                return httpx.Response(200, json={"result": {"success": True}})
            return httpx.Response(200, json={"result": {}})

        client = ArenaClient("http://lobby.test")
        client.arena_url = "http://arena.test"
        client.session_key = "sk-test"
        client._arena = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": f"Bearer {client.session_key}"},
        )

        # Stub backend that never gets called because no asset names exist.
        class _StubBackend:
            def describe(self) -> str:
                return "stub"

            async def complete(self, system: str, user: str) -> str:
                return "{}"

        brain = Brain(backend=_StubBackend(), target_planet="sol-hub", decision_interval=20)

        # Drive the contract: kick play() off; assert NO tool calls happen
        # while the preamble hasn't landed. Then signal connection_ready
        # and confirm play() proceeds.
        play_task = asyncio.create_task(brain.play(client))

        # Yield a few times to give play() a chance to (incorrectly) call
        # tools if the contract is broken.
        for _ in range(20):
            await asyncio.sleep(0)
        assert events_seen == [], (
            f"brain.play issued tool calls before connection_ready: {events_seen}"
        )

        # Now release the preamble; brain should proceed up to sim_started.wait().
        client.connection_id = "conn-int"
        client.connection_ready.set()

        # Wait until brain has at least claimed the planet.
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            if "claim_planet" in events_seen:
                break
            await asyncio.sleep(0.01)

        # Stop the brain — sim never starts in this test.
        play_task.cancel()
        try:
            await play_task
        except (asyncio.CancelledError, Exception):
            pass

        # After the preamble, get_simulation_info and claim_planet should
        # both have been issued, in that order.
        assert "get_simulation_info" in events_seen
        assert "claim_planet" in events_seen
        gsi_idx = events_seen.index("get_simulation_info")
        cp_idx = events_seen.index("claim_planet")
        assert gsi_idx < cp_idx

    asyncio.run(run())
