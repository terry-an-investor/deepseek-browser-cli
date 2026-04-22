# Agent Playbook (Canonical)

This file is the implementation playbook for AI coding agents.
`README.md` is user-facing product documentation; do not copy its interface/quick-start content here.

## Scope and Source of Truth

- Use `README.md` for install, usage, and interface descriptions.
- Use this file for coding constraints, architecture invariants, and test gates.
- If README and code disagree, trust the code and update docs in the same change.

## Working Rules

1. Use Python 3.11+ and `uv` commands only (never bare `pip`).
2. Add type hints for public APIs.
3. Keep imports explicit; avoid wildcard/package-level shortcuts.
4. New public APIs belong in their semantic module (`primitives`, `semantics`, `chat`). Re-export through `deepseek_model.py` if external callers need it.
5. Layer 2 actions rely on multiple fallback strategies (a11y tree → JS → broader search). Maintain this pattern for resilience against UI changes.

## Where Code Lives

See README for architecture overview. When editing, put new code in the right layer:

| Layer | File | What goes here |
|-------|------|---------------|
| L1 | `primitives.py` | CDP bridge, caching, generic a11y ops |
| L2 | `semantics.py` | DeepSeek-specific actions, fallback strategies |
| L3 | `chat.py` | Conversation workflows, polling loops |
| — | `models.py` | Dataclasses |
| — | `agent_bridge.py` | JSON observe/act for AI agents |
| — | `mcp_server.py` | MCP tools |
| — | `deepseek_model.py` | Backward-compat shim only |

## Design Principles

- agent-browser subprocess calls are expensive. Optimize hot paths by reading existing caching and fast-state patterns before adding new calls.
- Prefer semantic text markers (e.g. "已思考") and ARIA roles over CSS class names.
- Message history is virtualized; only visible messages are in the DOM.
- Mode switching requires the initial page (before any user message is sent).

## Test Requirements

- Safe suite: `uv run pytest tests/ -q`
- Live browser suite: `RUN_DEEPSEEK_E2E=1 uv run pytest -q`
- Changes to polling/retry/state detection must include a regression test in `tests/test_regressions.py` (use `FakeA11y` stubs).

## Debug Workflow

When an interaction fails, inspect in this order:

1. `snapshot` to inspect a11y tree output.
2. `eval` for targeted page-state probing.
3. `get url` to verify navigation/session state.

## Change Checklist

Before finishing, verify all items:

- Relevant tests pass or are intentionally skipped with reason.
- No added redundant CDP calls in critical paths.
- Cache invalidation still occurs on mutating actions.
- FakeA11y compatibility remains intact.
- Docs updated only where behavior/contract changed.
