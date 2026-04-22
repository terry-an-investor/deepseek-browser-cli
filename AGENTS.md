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
4. Preserve backward compatibility in `deepseek_browser_cli/deepseek_model.py`.
5. Avoid adding fallback paths unless clearly required by runtime variance.

## Performance and Reliability Invariants

1. CDP subprocesses are the bottleneck: avoid redundant calls in hot loops.
2. Prefer `get_fast_state()` for polling; do not replace it with heavier snapshots.
3. Invalidate caches after mutations (`click`, `type`, `press`, `eval_js`).
4. Keep `DeepSeekSemantics._eval_json()` compatible with doubles that may not implement `eval_json`.
5. Do not rely on volatile CSS classes; prefer semantic text/ARIA cues.
6. Treat message history as virtualized: visible message count may differ from total.
7. Mode switching is only reliable before the first user message is sent.

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
