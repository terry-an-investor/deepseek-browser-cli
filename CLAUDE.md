# Project Instructions

## Python Environment

- Use `uv` for all Python dependency management and virtual environment operations
- Never use `pip` directly — always `uv pip` or `uv add`/`uv remove`
- Run commands through `uv run` or activate `.venv` — do not rely on system Python
- Update `uv.lock` after any dependency change

## Debugging

- Use Chrome DevTools Protocol (CDP) for all browser automation debugging
- The project wraps `agent-browser` which exposes CDP commands via CLI
- When debugging page interaction issues:
  1. Use `snapshot` to dump the accessibility tree and inspect element structure
  2. Use `eval` to run JavaScript in the page context for DOM queries
  3. Check `get url` to verify navigation state
- Avoid relying on specific CSS class names where possible — prefer semantic markers (e.g., "已思考" indicator) or stable ARIA roles
