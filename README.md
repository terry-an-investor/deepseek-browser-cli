# DeepSeek Browser CLI

Semantic automation for [chat.deepseek.com](https://chat.deepseek.com) over Chrome DevTools Protocol (CDP).

Instead of driving brittle selectors directly, this project exposes DeepSeek-aware operations such as:

- send message and wait for reply
- read current conversation state
- switch mode (`instant` / `expert`)
- toggle deep thinking and web search
- interact with latest response actions

It is designed to be used in four ways:

- `deepseek-browser`: interactive chat + passthrough to `agent-browser`
- `deepseek-agent`: JSON observe/act/turn CLI for agents
- `deepseek-mcp`: MCP server (stdio transport)
- Python API: `DeepSeekChat`, `MultiRoundChat`, `DeepSeekAgentBridge`

> **For educational and research use only**
>
> Use this responsibly and in accordance with DeepSeek Terms of Service.

## Installation

Requirements:

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- [agent-browser](https://www.npmjs.com/package/agent-browser)
- Google Chrome

```bash
# Install CDP CLI
npm install -g agent-browser

# Install project dependencies
uv sync

# Install test/dev dependencies
uv sync --group dev
```

## First-Time CDP Setup (Key Blocker)

If MCP fails to connect, this is usually the issue.

Chrome remote debugging must run with a non-default user-data directory. MCP now auto-launches a dedicated Chrome instance with persistent profile by default:

- default CDP port: `9333`
- default profile directory: `~/.deepseek-mcp-chrome`
- default launch target: `/Applications/Google Chrome.app` (macOS, via `open -na`)

First run flow:

1. Start MCP server: `uv run deepseek-mcp`
2. Dedicated Chrome launches automatically
3. Log into DeepSeek once in that window
4. Later runs reuse the same profile and keep login state

Supported overrides:

- `DEEPSEEK_CDP_PORT`
- `DEEPSEEK_CHROME_PATH`
- `DEEPSEEK_CHROME_USER_DATA_DIR`

### Parallel Daily Chrome Tip

Your daily Chrome and MCP Chrome can run in parallel.

Manual launch equivalent:

```bash
open -na "/Applications/Google Chrome.app" --args \
  --remote-debugging-port=9333 \
  --user-data-dir="$HOME/.deepseek-mcp-chrome"

DEEPSEEK_CDP_PORT=9333 uv run deepseek-mcp
```

## Testing

```bash
# Safe default (live browser tests skipped unless enabled)
uv run pytest -q

# Run live end-to-end tests
RUN_DEEPSEEK_E2E=1 uv run pytest -q
```

## CLI Usage

### Interactive chat

```bash
uv run deepseek-browser --interactive
uv run deepseek-browser --interactive --mode expert --thinking
uv run deepseek-browser --interactive --auto-connect
```

Slash commands in interactive mode:

- `/new`
- `/mode quick|expert`
- `/think on|off`
- `/search on|off`
- `/history`
- `/export`
- `/copy`, `/regen`, `/like`, `/dislike`, `/share`

### JSON agent CLI

```bash
# Observe current page state
uv run deepseek-agent --session test --pretty observe

# Execute one action
uv run deepseek-agent --session test act \
  --action '{"type": "send", "params": {"text": "hi"}}'

# Run a full turn
uv run deepseek-agent --session test turn "What is 2+2?"
```

### Passthrough to agent-browser

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

### MCP behavior

- Ensures a CDP endpoint is available on startup
- Auto-launches dedicated Chrome with persistent profile when needed
- Uses low-latency chat polling (default `poll_interval=0.25`)
- Emits incremental progress/log messages during streaming for clients that surface them
- Applies mode defaults for think/search toggles on startup/mode switch/new chat

### MCP tools

| Tool | Args | Notes |
|------|------|-------|
| `deepseek_chat` | `message: str`, `timeout: float=120`, `poll_interval: float=0.25` | Returns JSON with `success`, `response`, optional `thinking` |
| `deepseek_observe` | none | Full page observation JSON |
| `deepseek_toggle` | `feature: deep_thinking \| web_search` | Manual toggle control |
| `deepseek_mode` | `mode: expert \| instant \| quick` | Mode switch on initial page + default toggle application |
| `deepseek_new_chat` | none | Start fresh dialog + default toggle application |

### Mode-based default toggles

Current defaults:

- `instant`: `deep_thinking=true`, `web_search=true`
- `expert`: `deep_thinking=true`, `web_search=true`

Override with:

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

## Python API Examples

### `DeepSeekChat`

```python
from deepseek_browser_cli.deepseek_model import ChatMode, DeepSeekChat

chat = DeepSeekChat(session="default", auto_connect=True)
chat.goto("/")
chat.select_mode(ChatMode.EXPERT)

chat.send_message("Explain quantum computing in one paragraph")
response = chat.wait_for_response(timeout=60)
print(response)
```

### `MultiRoundChat`

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

### `DeepSeekAgentBridge`

```python
from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge

bridge = DeepSeekAgentBridge(session="agent", auto_connect=True)
obs = bridge.observe()
print(obs["page_state"])

result = bridge.run_turn("What is 2+2?")
print(result["assistant_response"])
```

## Performance Notes

Recent hot-path optimizations include:

- short-lived `snapshot()` and `get_url()` caching in `A11yPrimitives`
- centralized `eval_json()` parsing path
- lightweight `get_fast_state()` for tight polling loops
- adaptive polling backoff in response-wait loops
- reduced redundant JS eval in bridge page-state observation

Remaining biggest opportunity:

- move from one-subprocess-per-CDP-call to a persistent `agent-browser` process or direct CDP client

## Debugging Tips

Use CDP primitives directly when behavior is unclear:

```bash
# Accessibility tree
uv run deepseek-browser --session debug snapshot

# URL and navigation state
uv run deepseek-browser --session debug get url

# Quick DOM probe
uv run deepseek-browser --session debug eval '
(() => JSON.stringify({
  hasTextarea: !!document.querySelector("textarea"),
  title: document.title,
  url: location.href
}))()'
```

Recommended order:

1. `snapshot`
2. `eval`
3. `get url`

## Known Limitations

- mode switching only works on initial page
- file upload is not fully implemented
- login/captcha is not automated
- major DeepSeek UI changes may require selector/strategy updates

## License

MIT
