# Agent Playbook

## Read Order

1. `README.md` — product overview, install, interfaces
2. Source files — implementation is truth
3. This file — only when stuck on workflow or constraints

## Where Code Lives

| File | Layer | Purpose |
|------|-------|---------|
| `primitives.py` | L1 | CDP bridge, caching |
| `semantics.py` | L2 | DeepSeek actions, fallback strategies |
| `chat.py` | L3 | Conversation loops, polling |
| `models.py` | — | Dataclasses |
| `agent_bridge.py` | — | JSON observe/act for agents |
| `mcp_server.py` | — | MCP tools |
| `deepseek_model.py` | — | Backward-compat shim only |

## Constraints

- agent-browser subprocess calls are expensive. Read existing caching and fast-state patterns in hot paths before adding new CDP calls.
- Mode switching only works on the initial page (before any user message).
- `FakeA11y` test stubs only implement `eval_js`, not `eval_json`. Keep `_eval_json()` dual-path compatible.

## Test Gate

- `uv run pytest tests/ -q`
- Polling/retry changes need a regression test in `tests/test_regressions.py`.
