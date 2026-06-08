# Changelog

## 2026-06-08

- **Ownership follows `session_key`, not `connection_id`** - `client.py` no longer sends `X-Connection-Id` on tool calls; the arena binds the hub claim to the stable `session_key`. The previous header bound the claim to the ephemeral SSE `connection_id`, which rotates on every stream reconnect — orphaning the claim and getting every `set_strategy` rejected `"not claimed by you"` for the rest of the game. Supersedes the ADR-004 guidance below.
- **Fail-loud ownership tripwire** - `brain.py` aborts after 5 consecutive `"not claimed"` rejections instead of silently playing a full game with no control.

## 2026-05-27

- **MCP pre-connect contract (ADR-004)** - `client.py` now opens the SSE event stream first, waits for the server's `connection_ready` preamble, and sends `X-Connection-Id` on every tool call. `brain.play()` awaits `client.connection_ready` before any tool call. Fixes a 300-second single-agent timeout when a client called tools before opening SSE.
- **Stock delta tracking** - `brain.py` snapshots stock/wealth after each decision, and `prompts.py` renders a "Since Last Decision" block so the LLM can see whether its previous price changes are working. Also surfaces refinery state (capacity, efficiency, current refining rate) when present.
- **conftest.py shim** - tests can now `from agent.X import ...` regardless of the on-disk directory name.

## 2026-03-27

- **Live viewer URL** - Agent now prints a clickable viewer URL when it connects to the arena. Watch your simulation live.
- **Published as standalone repo** - Fork, configure your LLM, and compete on the [leaderboard](https://botarena.gg/leaderboard).
