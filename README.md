# DeepSeek Browser CLI

A semantic automation layer for [chat.deepseek.com](https://chat.deepseek.com) built on Chrome DevTools Protocol (CDP). Unlike generic browser automation tools that interact with raw DOM elements, this CLI provides **semantic actions** — high-level operations that understand the DeepSeek chat UI's meaning (send message, toggle deep thinking, copy response, etc.).

Designed to be used as an **MCP server**, **AI skill**, or **programmatic API**.

> **⚠️ For Educational and Research Purposes Only**
>
> This project is intended solely for learning, research, and personal automation experiments. Please use it responsibly and in accordance with DeepSeek's Terms of Service. Do not abuse the service, overload the platform, or use this tool for any malicious or unauthorized purposes.
>
> By using this software, you agree to comply with all applicable laws and the terms of service of the platforms you interact with. The authors assume no liability for misuse.

---

## Architecture

```
┌─────────────────────────────────────────┐
│  MCP Server (deepseek-mcp)              │  ← stdio transport for Claude, etc.
│  - deepseek_chat, deepseek_observe      │
│  - deepseek_toggle, deepseek_mode       │
├─────────────────────────────────────────┤
│  Agent Bridge                           │  ← AI agent integration
│  - observe() -> JSON state              │    (discover, decide, act)
│  - act() -> generic primitives          │
├─────────────────────────────────────────┤
│  Layer 3b: MultiRoundChat               │  ← Stateful conversations
│  - send(), send_streaming()             │    (history, export, regenerate)
│  - export_markdown(), get_history()     │
├─────────────────────────────────────────┤
│  Layer 3: DeepSeekChat                  │  ← High-level workflows
│  - wait_for_response()                  │    (send → wait → extract)
│  - toggle_deep_thinking()               │
├─────────────────────────────────────────┤
│  Layer 2: DeepSeekSemantics             │  ← Page-specific actions
│  - send_message(), toggle_deep_thinking │    with fallback strategies
│  - copy_response(), like_response()     │
├─────────────────────────────────────────┤
│  Layer 1: A11yPrimitives                │  ← Generic CDP operations
│  - snapshot(), click_by_ref(), eval_js()│
└─────────────────────────────────────────┘
```

**Key design principle**: Every Layer 2 action has multiple fallback strategies. If the accessibility tree reference (`@ref`) fails, it falls back to CSS selectors, then to JavaScript DOM manipulation.

---

## Installation

Requires Python 3.11+, [uv](https://github.com/astral-sh/uv), and [agent-browser](https://www.npmjs.com/package/agent-browser) (npm).

```bash
# 1. Install agent-browser (provides CDP CLI)
npm install -g agent-browser

# 2. Clone and install
uv pip install -e .
# or
uv sync
```

---

## MCP Server

The fastest way to use this project is as an MCP server. It exposes 5 tools over stdio transport.

### Running

```bash
deepseek-mcp
# or
uv run python -m deepseek_browser_cli.mcp_server
```

### Tools

| Tool | Args | Description |
|------|------|-------------|
| `deepseek_chat` | `message: str` | Send a message, wait for response, return JSON with `response` and optional `thinking` |
| `deepseek_observe` | — | Return full page state as JSON |
| `deepseek_toggle` | `feature: str` | `"deep_thinking"` or `"web_search"` |
| `deepseek_mode` | `mode: str` | `"expert"` or `"instant"` (only on new dialog) |
| `deepseek_new_chat` | — | Start a fresh conversation |

### Claude Code Integration

Add to `~/.claude/CLAUDE.md`:

```json
{
  "mcpServers": {
    "deepseek": {
      "command": "uv",
      "args": ["run", "python", "-m", "deepseek_browser_cli.mcp_server"],
      "cwd": "/path/to/deepseek-browser-cli"
    }
  }
}
```

### Agent Usage Pattern

```
# Start new dialog in expert mode with thinking enabled
deepseek_new_chat()
deepseek_mode("expert")
deepseek_toggle("deep_thinking")

# Chat — each call is one turn in the same conversation
deepseek_chat("Explain quantum computing")
deepseek_chat("Now explain it to a 5-year-old")
```

---

## CLI Usage

### Interactive Chat

```bash
# Interactive multi-round chat (streaming, unlimited turns)
deepseek-browser --interactive
deepseek-browser -i --mode expert --thinking
```

### Agent CLI (JSON I/O)

```bash
# Observe page state
deepseek-agent observe --session test --pretty

# Execute an action
deepseek-agent act --session test --action '{"type": "send", "params": {"text": "hi"}}'

# Run one complete turn
deepseek-agent turn --session test "What is 2+2?"
```

---

## Programmatic API

### Quick Start

```python
from deepseek_browser_cli.deepseek_model import DeepSeekChat, ChatMode

chat = DeepSeekChat(session="default", auto_connect=True)
chat.goto("/")
chat.select_mode(ChatMode.EXPERT)

chat.send_message("Explain quantum computing in one paragraph")
response = chat.wait_for_response(timeout=60)
print(response)
```

### Multi-Round Chat

```python
from deepseek_browser_cli.deepseek_model import MultiRoundChat, ChatMode

chat = MultiRoundChat(
    session="my-chat",
    auto_connect=True,
    mode=ChatMode.EXPERT,
    enable_thinking=True,
    enable_search=False,
)

# Single turn (blocks until complete response)
turn = chat.send("Explain quantum computing in one paragraph")
print(turn.assistant_response)
if turn.thinking_trace:
    print(f"Thought for {turn.thinking_trace.time}")

# Streaming turn (real-time token delivery)
def on_token(delta, full_text):
    print(delta, end="", flush=True)

turn = chat.send_streaming("Write a haiku about AI", on_token=on_token)

# Export
with open("chat.md", "w") as f:
    f.write(chat.export_markdown())
```

### Agent Bridge (Discovery-Based)

```python
from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge

bridge = DeepSeekAgentBridge(session="agent", auto_connect=True)

# 1. OBSERVE: Get complete page state as JSON
obs = bridge.observe()

# 2. ACT: Execute generic action based on observation
result = bridge.act({"type": "send", "params": {"text": "What is 2+2?"}})

# 3. OBSERVE again to verify
obs = bridge.observe()
print(obs["last_response"]["content"])

# 4. Click discovered action
bridge.act({"type": "click_action", "params": {"action": "copy"}})
```

The `AGENT_SYSTEM_PROMPT` constant provides a complete prompt you can give to an LLM to let it control the bridge autonomously.

---

## Resilience Design

Every semantic action implements **3 fallback strategies**:

1. **Accessibility tree reference** (`@ref`) — fastest, most reliable
2. **CSS selector / DOM query** — survives minor UI changes
3. **JavaScript DOM manipulation** — bypasses all accessibility issues

Plus:
- **Virtual scrolling aware** — message extraction works with DeepSeek's virtual list
- **Language agnostic** — works with both Chinese and English UI
- **Stuck detection** — nudges the page if DeepSeek hangs (free tier can be slow)

---

## Data Models

```python
class ChatMode(str, Enum):
    QUICK = "快速模式"   # also accepts "instant"
    EXPERT = "专家模式"  # also accepts "expert"

@dataclass
class PageState:
    url: str
    is_initial_page: bool
    has_input: bool
    is_streaming: bool
    message_count: int
    mode: str              # "instant", "expert", or "unknown"
    deep_thinking_enabled: bool
    web_search_enabled: bool
```

---

## Environment

- **Python**: 3.11+
- **Browser**: Chrome/Chromium with remote debugging
- **agent-browser**: npm package providing CDP CLI wrapper
- **OS**: macOS (tested), Linux (supported via `agent-browser` binaries)

---

## Known Limitations

- **Mode switching**: Can only be done on a new dialog (before the first message). Once a conversation starts, the mode is locked.
- **File upload**: Triggers the upload dialog but cannot complete file selection without OS-level automation
- **Login/verification**: No automated handling for captcha or login flows
- **UI redesigns**: Class-name-based JS fallbacks may break on major UI updates
- **Free tier slowness**: DeepSeek's free tier can randomly hang; the MCP server has retry logic but timeouts may still occur

---

## License

MIT
