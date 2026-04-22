# DeepSeek Browser CLI

Semantic browser automation for [chat.deepseek.com](https://chat.deepseek.com) via Chrome DevTools Protocol (CDP).

Instead of brittle CSS selectors, this toolkit exposes DeepSeek-aware operations: send messages, read conversation state, switch modes, toggle features, and interact with responses. Multiple interfaces are provided so you can use it interactively, programmatically, or as an MCP server.

> **For educational and research use only.**
> Use responsibly and in accordance with DeepSeek's Terms of Service.

## Quick Start

### Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- [agent-browser](https://www.npmjs.com/package/agent-browser) (`npm install -g agent-browser`)
- Google Chrome

```bash
# Install dependencies
uv sync

# Run interactive chat
uv run deepseek-browser --interactive

# Or start the MCP server
uv run deepseek-mcp
```

### First-Time Setup

MCP auto-launches a dedicated Chrome instance with a persistent profile:

- **CDP port**: `9333`
- **Profile**: `~/.deepseek-mcp-chrome`
- **Chrome path**: `/Applications/Google Chrome.app` (macOS)

1. Start MCP: `uv run deepseek-mcp`
2. Chrome opens automatically — log into DeepSeek once
3. Profile persists across restarts

Override via environment variables:
- `DEEPSEEK_CDP_PORT`
- `DEEPSEEK_CHROME_PATH`
- `DEEPSEEK_CHROME_USER_DATA_DIR`

## Architecture

The codebase follows a three-layer architecture:

| Layer | Module | Responsibility |
|-------|--------|---------------|
| **L1** | `primitives` | Generic CDP operations via `agent-browser` |
| **L2** | `semantics` | DeepSeek-specific page actions with fallback strategies |
| **L3** | `chat` | High-level conversational workflows |

Additional interfaces:
- `agent_bridge` — JSON observe/act pattern for AI agents
- `mcp_server` — MCP stdio server for tool clients
- `cli` — Interactive terminal chat + raw CDP passthrough

## Interfaces

### 1. Interactive CLI

```bash
uv run deepseek-browser --interactive --mode expert --thinking
```

Slash commands:

| Command | Action |
|---------|--------|
| `/new` | Start fresh conversation |
| `/mode quick\|expert` | Switch chat mode |
| `/think on\|off` | Toggle deep thinking |
| `/search on\|off` | Toggle web search |
| `/history` | Show conversation history |
| `/export` | Save as markdown |
| `/copy`, `/regen`, `/like`, `/dislike`, `/share` | Response actions |

### 2. JSON Agent CLI

```bash
# Observe page state
uv run deepseek-agent --session test --pretty observe

# Execute one action
uv run deepseek-agent --session test act \
  --action '{"type": "send", "params": {"text": "hi"}}'

# Run a full turn
uv run deepseek-agent --session test turn "What is 2+2?"
```

### 3. MCP Server

```bash
uv run deepseek-mcp
```

**Tools:**

| Tool | Args | Returns |
|------|------|---------|
| `deepseek_chat` | `message`, `timeout=120`, `poll_interval=0.25` | `{success, response, thinking?}` |
| `deepseek_observe` | none | Full page state JSON |
| `deepseek_toggle` | `feature: deep_thinking \| web_search` | Updated toggle state |
| `deepseek_mode` | `mode: expert \| instant \| quick` | Mode + default toggles |
| `deepseek_new_chat` | none | Fresh dialog with defaults applied |

**Mode defaults** (override via env vars):

| Mode | Deep Thinking | Web Search |
|------|--------------|------------|
| `instant` | `true` | `true` |
| `expert` | `true` | `true` |

Env vars: `DEEPSEEK_DEFAULT_THINKING_INSTANT`, `DEEPSEEK_DEFAULT_SEARCH_INSTANT`, `DEEPSEEK_DEFAULT_THINKING_EXPERT`, `DEEPSEEK_DEFAULT_SEARCH_EXPERT`

**MCP client config example:**

```json
{
  "mcpServers": {
    "deepseek": {
      "command": "uv",
      "args": ["run", "python", "-m", "deepseek_browser_cli.mcp_server"],
      "cwd": "/path/to/deepseek-web-cli"
    }
  }
}
```

### 4. Python API

```python
from deepseek_browser_cli import DeepSeekChat, ChatMode, MultiRoundChat

# Single-turn chat
chat = DeepSeekChat(session="default", auto_connect=True)
chat.goto("/")
chat.select_mode(ChatMode.EXPERT)
chat.send_message("Explain quantum computing")
response = chat.wait_for_response(timeout=60)

# Multi-round with streaming
chat = MultiRoundChat(
    session="my-chat",
    auto_connect=True,
    mode=ChatMode.EXPERT,
    enable_thinking=True,
)

def on_token(delta, full):
    print(delta, end="", flush=True)

turn = chat.send_streaming("Write a haiku about AI", on_token=on_token)
```

## Project Structure

```
deepseek_browser_cli/
├── __init__.py          # Package exports
├── models.py            # Data classes (ChatMode, Message, PageState, etc.)
├── primitives.py        # Layer 1: A11yPrimitives (CDP bridge)
├── semantics.py         # Layer 2: DeepSeekSemantics (page actions)
├── chat.py              # Layer 3: DeepSeekChat, MultiRoundChat
├── agent_bridge.py      # Observer + Actor + Bridge for AI agents
├── agent_cli.py         # JSON CLI for agents
├── mcp_server.py        # MCP stdio server
└── cli/
    └── __init__.py      # Interactive CLI + CDP passthrough

tests/
├── test_regressions.py  # Offline unit tests
├── test_mcp.py          # MCP e2e tests
├── test_mcp_mode.py     # Mode switching tests
├── test_mcp_multiround.py
├── test_multiround.py   # Agent bridge stress tests
└── test_mcp_mode_debug.py
```

## Performance

Recent optimizations:

- **Snapshot/URL caching** — `A11yPrimitives` caches snapshots for 150ms and URLs for 2s, eliminating redundant subprocess calls within observation cycles
- **`eval_json()`** — Centralized JSON parsing path for JS eval results
- **`get_fast_state()`** — Lightweight single-JS-eval state check for tight polling loops (replaces expensive `get_page_state()` in hot paths)
- **Adaptive polling backoff** — Response wait loops start at 0.2s and back off to 1s, reducing CPU while maintaining low latency
- **Reduced redundant JS evals** — `_observe_page_state()` no longer runs duplicate toggle detection

Biggest remaining opportunity: move from one-subprocess-per-CDP-call to a persistent `agent-browser` process or direct WebSocket CDP client.

## Testing

```bash
# Safe default (e2e tests skipped)
uv run pytest -q

# Run live browser tests
RUN_DEEPSEEK_E2E=1 uv run pytest -q
```

## Debugging

Use CDP primitives directly:

```bash
# Accessibility tree
uv run deepseek-browser --session debug snapshot

# DOM probe
uv run deepseek-browser --session debug eval '
(() => JSON.stringify({
  hasTextarea: !!document.querySelector("textarea"),
  title: document.title
}))()'
```

## Known Limitations

- Mode switching only works on the initial page
- File upload is not fully implemented
- Login/captcha is not automated
- Major DeepSeek UI changes may require selector updates

## License

MIT
