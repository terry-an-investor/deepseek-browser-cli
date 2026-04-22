# DeepSeek Browser CLI

Semantic automation for [chat.deepseek.com](https://chat.deepseek.com) over Chrome DevTools Protocol (CDP).

This project provides DeepSeek-aware actions (send message, switch mode, toggle features, read last response) instead of forcing callers to operate raw DOM selectors.

It can be used as:

- `deepseek-browser`: interactive chat plus `agent-browser` passthrough
- `deepseek-agent`: JSON observe/act/turn interface for agents
- `deepseek-mcp`: stdio MCP server for tool clients
- Python API: `DeepSeekChat`, `MultiRoundChat`, `DeepSeekAgentBridge`

> **For educational and research use only**
>
> Use responsibly and follow DeepSeek Terms of Service. Do not abuse the platform or use this project for unauthorized automation.

## Installation

Requirements:

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- [agent-browser](https://www.npmjs.com/package/agent-browser)
- Google Chrome installed

```bash
# Install CDP CLI
npm install -g agent-browser

# Install runtime dependencies
uv sync

# Install dev/test dependencies
uv sync --group dev
```

## First-Time CDP Setup (Key Blocker)

If MCP cannot connect, this is usually the blocker.

Chrome remote debugging requires a non-default user-data-dir. The server now auto-launches Chrome with a persistent profile by default, so login persists:

- Default CDP port: `9222`
- Default persistent profile: `~/.deepseek-mcp-chrome`

First run flow:

1. Start MCP server (`uv run deepseek-mcp`).
2. A dedicated Chrome instance is auto-launched with CDP and persistent profile.
3. Log into DeepSeek once in that Chrome window.
4. Future runs reuse the same profile and keep login state.

Optional environment overrides:

- `DEEPSEEK_CDP_PORT`
- `DEEPSEEK_CHROME_PATH`
- `DEEPSEEK_CHROME_USER_DATA_DIR`

## Testing

```bash
# Safe default (integration tests are skipped unless enabled)
uv run pytest -q

# Run live integration tests
RUN_DEEPSEEK_E2E=1 uv run pytest -q
```

## CLI

### Interactive chat

```bash
uv run deepseek-browser --interactive
uv run deepseek-browser --interactive --mode expert --thinking
uv run deepseek-browser --interactive --auto-connect
```

Slash commands:

- `/new` start new conversation
- `/mode quick|expert` switch mode
- `/think on|off` toggle deep thinking
- `/search on|off` toggle web search
- `/history` print local history
- `/export` export markdown
- `/copy`, `/regen`, `/like`, `/dislike`, `/share` response actions

### JSON agent CLI

```bash
# Observe current state
uv run deepseek-agent --session test --pretty observe

# Execute one action
uv run deepseek-agent --session test act \
  --action '{"type": "send", "params": {"text": "hi"}}'

# Run one full turn
uv run deepseek-agent --session test turn "What is 2+2?"
```

### agent-browser passthrough

```bash
uv run deepseek-browser --session test snapshot
uv run deepseek-browser --session test get url
```

## MCP Server

Run:

```bash
uv run deepseek-mcp
# or
uv run python -m deepseek_browser_cli.mcp_server
```

### Current MCP behavior

- Auto-ensures Chrome CDP is available.
- Auto-launches Chrome with persistent profile (`~/.deepseek-mcp-chrome`) if needed.
- Uses low-latency chat polling (default `poll_interval=0.25`) for faster response completion.
- Emits incremental progress/log messages during streaming for clients that surface MCP logs.

### Tools

| Tool | Args | Notes |
|------|------|-------|
| `deepseek_chat` | `message: str`, `timeout: float=120`, `poll_interval: float=0.25` | Send one message and return JSON (`success`, `response`, optional `thinking`) |
| `deepseek_observe` | none | Return full observed page state |
| `deepseek_toggle` | `feature: deep_thinking | web_search` | Toggle one feature manually |
| `deepseek_mode` | `mode: expert | instant | quick` | Switch mode on initial page and auto-apply mode defaults |
| `deepseek_new_chat` | none | Start new dialog and auto-apply mode defaults |

### Mode-based default toggles

MCP now auto-applies configured defaults for both modes (`instant` and `expert`) on startup, mode switch, and new chat.

Default values:

- `instant`: `deep_thinking=true`, `web_search=true`
- `expert`: `deep_thinking=true`, `web_search=true`

Environment overrides:

- `DEEPSEEK_DEFAULT_THINKING_INSTANT`
- `DEEPSEEK_DEFAULT_SEARCH_INSTANT`
- `DEEPSEEK_DEFAULT_THINKING_EXPERT`
- `DEEPSEEK_DEFAULT_SEARCH_EXPERT`

### Example MCP client config

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

## Python API

### DeepSeekChat

```python
from deepseek_browser_cli.deepseek_model import ChatMode, DeepSeekChat

chat = DeepSeekChat(session="default", auto_connect=True)
chat.goto("/")
chat.select_mode(ChatMode.EXPERT)

chat.send_message("Explain quantum computing in one paragraph")
response = chat.wait_for_response(timeout=60)
print(response)
```

### MultiRoundChat

```python
from deepseek_browser_cli.deepseek_model import ChatMode, MultiRoundChat

chat = MultiRoundChat(
    session="my-chat",
    auto_connect=True,
    mode=ChatMode.EXPERT,
    enable_thinking=True,
    enable_search=False,
)

def on_token(delta, full_text):
    print(delta, end="", flush=True)

turn = chat.send_streaming("Write a haiku about AI", on_token=on_token)
print("\\n---")
print(turn.assistant_response)
```

### DeepSeekAgentBridge

```python
from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge

bridge = DeepSeekAgentBridge(session="agent", auto_connect=True)
obs = bridge.observe()
print(obs["page_state"])

result = bridge.run_turn("What is 2+2?")
print(result["assistant_response"])
```

## Debugging

Use CDP primitives through `agent-browser`:

```bash
# Accessibility tree
uv run deepseek-browser --session debug snapshot

# Current URL
uv run deepseek-browser --session debug get url

# Direct DOM inspection
uv run deepseek-browser --session debug eval '
(() => {
  const textarea = document.querySelector("textarea");
  return JSON.stringify({
    hasTextarea: !!textarea,
    title: document.title,
    url: location.href
  });
})()
'
```

Recommended debugging order:

1. `snapshot`
2. `eval`
3. `get url`

Prefer semantic markers and ARIA roles over brittle CSS class assumptions.

## Known limitations

- Mode can only be switched on the initial page.
- File upload is not fully implemented.
- Login/captcha flows are not automated by this project.
- Major DeepSeek UI changes may still require fallback updates.

## License

MIT
