# DeepSeek Browser CLI

Semantic automation for [chat.deepseek.com](https://chat.deepseek.com) over Chrome DevTools Protocol (CDP).

Instead of driving raw DOM nodes directly, this project exposes DeepSeek-aware actions such as sending a message, switching mode, toggling deep thinking, reading the latest response, and exporting multi-turn chat history.

It is designed to be used in four ways:

- `deepseek-browser`: interactive chat plus `agent-browser` passthrough
- `deepseek-agent`: JSON observe/act/turn interface for agents
- `deepseek-mcp`: stdio MCP server
- Python API: `DeepSeekChat`, `MultiRoundChat`, `DeepSeekAgentBridge`

> **For educational and research use only**
>
> Use this responsibly and in line with DeepSeek's Terms of Service. Do not abuse the platform or use this project for malicious or unauthorized automation.

## Installation

Requirements:

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- [agent-browser](https://www.npmjs.com/package/agent-browser)
- A browser session already authenticated with DeepSeek

```bash
# Install the CDP CLI
npm install -g agent-browser

# Install runtime dependencies
uv sync

# Install test dependencies
uv sync --group dev
```

If you already have Chrome running with a logged-in DeepSeek tab, most commands can attach to it with `--auto-connect`.

## Testing

The test suite is live browser integration coverage. It requires `agent-browser`, a valid DeepSeek session, and explicit opt-in.

```bash
# Safe default: pytest runs, browser tests stay skipped
uv run pytest -q

# Run the live integration suite
RUN_DEEPSEEK_E2E=1 uv run pytest -q
```

## CLI

### Interactive chat

```bash
uv run deepseek-browser --interactive
uv run deepseek-browser --interactive --mode expert --thinking
uv run deepseek-browser --interactive --auto-connect
```

Useful slash commands in interactive mode:

- `/new` starts a fresh conversation
- `/mode quick|expert` switches mode
- `/think on|off` toggles deep thinking
- `/search on|off` toggles web search
- `/history` shows local turn history
- `/export` writes the conversation to markdown
- `/copy`, `/regen`, `/like`, `/dislike`, `/share` act on the latest response

### JSON agent CLI

`deepseek-agent` is meant for agents that want structured observations and generic actions.

```bash
# Observe current state
uv run deepseek-agent --session test --pretty observe

# Execute one action
uv run deepseek-agent --session test act \
  --action '{"type": "send", "params": {"text": "hi"}}'

# Run one full turn
uv run deepseek-agent --session test turn "What is 2+2?"
```

### `agent-browser` passthrough

Without `--interactive`, `deepseek-browser` forwards commands to `agent-browser`.

```bash
uv run deepseek-browser --session test snapshot
uv run deepseek-browser --session test get url
```

## MCP Server

Run the MCP server over stdio:

```bash
uv run deepseek-mcp
# or
uv run python -m deepseek_browser_cli.mcp_server
```

Exposed tools:

| Tool | Args | Notes |
|------|------|-------|
| `deepseek_chat` | `message: str` | Sends one message and returns JSON with `success`, `response`, and optional `thinking` |
| `deepseek_observe` | none | Returns full page state as JSON |
| `deepseek_toggle` | `feature: str` | `deep_thinking` or `web_search` |
| `deepseek_mode` | `mode: str` | `expert` or `instant`; `quick` is also accepted as an alias |
| `deepseek_new_chat` | none | Starts a fresh conversation |

Typical MCP flow:

```text
deepseek_new_chat()
deepseek_mode("expert")
deepseek_toggle("deep_thinking")
deepseek_chat("Explain quantum computing")
deepseek_chat("Now explain it to a 5-year-old")
```

Example client config:

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

### `DeepSeekChat`

Use `DeepSeekChat` when you want direct, synchronous control over a single conversation flow.

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

Use `MultiRoundChat` when you want setup, history, streaming callbacks, and export helpers.

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
print("\n---")
print(turn.assistant_response)

with open("chat.md", "w") as f:
    f.write(chat.export_markdown())
```

### `DeepSeekAgentBridge`

Use the bridge when an external agent wants to inspect live page state, choose an action, and verify the result.

```python
from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge

bridge = DeepSeekAgentBridge(session="agent", auto_connect=True)

obs = bridge.observe()
print(obs["page_state"])

result = bridge.run_turn("What is 2+2?")
print(result["assistant_response"])
```

## Debugging

This project wraps `agent-browser`, so the fastest way to debug interaction issues is through CDP primitives:

```bash
# Inspect the accessibility tree
uv run deepseek-browser --session debug snapshot

# Check the current page
uv run deepseek-browser --session debug get url

# Inspect DOM state directly
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

When debugging page behavior:

- Start with `snapshot` to inspect roles, refs, and visible structure
- Use `eval` for targeted DOM queries
- Use `get url` to confirm navigation state
- Prefer semantic markers and ARIA roles over brittle CSS-class assumptions

## Design Notes

The implementation is layered, but the practical split is:

- CDP and accessibility-tree primitives at the bottom
- DeepSeek-specific semantic actions in the middle
- Conversation workflows, agent bridge, and MCP server on top

DeepSeek-facing actions use multiple strategies in order: accessibility refs first, then DOM queries, then JavaScript fallbacks. That keeps the tool resilient to minor UI changes, virtualized message lists, and bilingual UI text.

## Limitations

- Mode switching only works on a new dialog.
- File upload is not fully implemented.
- Login, captcha, and account verification are not automated.
- Major DeepSeek UI changes can still break fallback logic.

## License

MIT
