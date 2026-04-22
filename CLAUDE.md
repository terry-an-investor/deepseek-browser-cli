# Agent Instructions

Context for AI assistants working on this codebase.

## Project

Semantic browser automation for chat.deepseek.com via Chrome DevTools Protocol (CDP). Provides multiple interfaces: interactive CLI, JSON agent bridge, MCP server, and Python API.

## Architecture

Three-layer design:

- **Layer 1 (`primitives`)** — `A11yPrimitives`: generic CDP operations via `agent-browser` subprocess. Every call spawns a new Node.js process. Caching is short-lived (snapshot 150ms, URL 2s) and invalidated on mutations.
- **Layer 2 (`semantics`)** — `DeepSeekSemantics`: page-specific actions with multiple fallback strategies. Each action tries a11y-tree first, then JS fallback. Uses `get_fast_state()` for tight polling loops.
- **Layer 3 (`chat`)** — `DeepSeekChat` / `MultiRoundChat`: high-level conversational workflows.

`agent_bridge.py` wraps observer + actor pattern for autonomous AI agents.
`mcp_server.py` exposes FastMCP tools with async polling.

## Coding Conventions

- Python 3.11+
- Use `uv` exclusively — never bare `pip`
- Type hints required on public APIs
- Prefer `Optional[dict]` over `dict | None` for compatibility
- Module imports: use explicit paths, not package-level wildcards
- New code must not break backward-compat shim in `deepseek_model.py`

## Testing

```bash
uv run pytest tests/ -q          # safe, e2e skipped
RUN_DEEPSEEK_E2E=1 uv run pytest  # live browser
```

- Unit tests go in `tests/test_regressions.py` using `FakeA11y` stubs
- E2E tests use live Chrome; skip unless `RUN_DEEPSEEK_E2E` is set
- Any change to polling logic needs a regression test with divergent counters

## Key Constraints

1. **Subprocess bottleneck** — every CDP call is a fresh `agent-browser` spawn. Do not add redundant calls in hot paths. Use caching and `get_fast_state()`.
2. **eval_json compat** — `DeepSeekSemantics._eval_json()` checks `hasattr(a11y, 'eval_json')` for test doubles. Do not assume the method exists on the a11y object.
3. **No CSS class reliance** — prefer semantic text markers or ARIA roles. DeepSeek changes classes frequently.
4. **Virtual list** — message history may not all be in DOM. `get_messages()` returns visible only; `get_all_messages()` scrolls to load.
5. **Mode switching** — only works on initial page (before first message sent).

## Debugging Workflow

When page interactions fail:

1. `snapshot` — dump a11y tree
2. `eval` — run JS in page context
3. `get url` — verify navigation state

Use semantic markers (e.g. "已思考") over CSS class names.

## Common Pitfalls

- Mixing `get_messages()` length with `get_fast_state()["message_count"]` — they can diverge
- Forgetting cache invalidation after mutations (`click`, `type`, `press`, `eval_js`)
- Adding fixed `time.sleep()` without adaptive backoff in polling loops
- Breaking `FakeA11y` compatibility by requiring new methods without fallback

## File Map

| File | Purpose |
|------|---------|
| `deepseek_browser_cli/primitives.py` | A11yPrimitives, caching, subprocess bridge |
| `deepseek_browser_cli/semantics.py` | DeepSeekSemantics, fallback strategies |
| `deepseek_browser_cli/chat.py` | DeepSeekChat, MultiRoundChat, polling loops |
| `deepseek_browser_cli/models.py` | All dataclasses |
| `deepseek_browser_cli/agent_bridge.py` | Observer/Actor/Bridge for AI agents |
| `deepseek_browser_cli/mcp_server.py` | MCP stdio server |
| `deepseek_browser_cli/cli/__init__.py` | Interactive terminal CLI |
| `deepseek_browser_cli/deepseek_model.py` | Backward-compat shim — do not add logic here |
