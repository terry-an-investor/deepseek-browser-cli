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

## Architecture Invariants

1. **agent-browser is the CDP bridge.** Every CDP operation spawns a fresh `agent-browser` subprocess. `A11yPrimitives` caches snapshots (150ms TTL) and URLs (2s TTL); mutating actions (`click`, `type`, `press`, `eval_js`) invalidate the cache.
2. **Polling uses lightweight `get_fast_state()`.** A single JS eval returning `{has_input, is_streaming, message_count}`. Full `get_page_state()` with snapshot + multi-strategy detection is only used when complete state is needed.
3. **Layer 2 fallback strategies are intentional.** Every semantic action (click, send, parse) tries a11y-tree first, then JS fallback, then broader search. This resilience against UI changes is core to the design.
4. **`_eval_json()` has dual paths.** Prefers `a11y.eval_json()` if available; falls back to `eval_js` + manual unwrapping. Test doubles (`FakeA11y`) only implement `eval_js`.
5. **Semantic selectors over CSS classes.** The codebase avoids brittle class names. Prefer text content ("已思考"), ARIA roles, or structural markers.
6. **Messages are virtualized.** DeepSeek only renders visible messages in the DOM. `get_messages()` returns viewport-visible messages; `get_all_messages()` scrolls to load history.
7. **Mode is set before the first message.** Mode switching requires the initial page (no conversation started). After the first user message, the mode is locked.

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
- Polling paths still use `get_fast_state()`, not heavier snapshots.
- Mutating actions still invalidate `A11yPrimitives` cache.
- `FakeA11y` test doubles still pass without implementing `eval_json`.
- Docs updated only where behavior/contract changed.
