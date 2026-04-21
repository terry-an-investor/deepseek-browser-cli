# DeepSeek Browser CLI

A semantic automation layer for [chat.deepseek.com](https://chat.deepseek.com) built on Chrome DevTools Protocol (CDP). Unlike generic browser automation tools that interact with raw DOM elements, this CLI provides **semantic actions** — high-level operations that understand the DeepSeek chat UI's meaning (send message, toggle deep thinking, copy response, etc.).

Designed to be wrapped as an **MCP server** or **AI skill**.

---

## Architecture

Three-layer design separating concerns from raw browser primitives to user workflows:

```
┌─────────────────────────────────────────┐
│  Layer 3: DeepSeekChat                  │  ← High-level workflows
│  - wait_for_response()                  │    (send → wait → extract)
│  - full_conversation_turn()             │
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

```bash
# Requires Python 3.11+, uv, and agent-browser (npm)
npm install -g agent-browser
uv pip install -e .
```

---

## CLI Usage

```bash
# Interactive multi-round chat
deepseek-browser --interactive

# One-shot command through agent-browser
deepseek-browser snapshot
deepseek-browser eval "document.title"
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

### Page State

```python
state = chat.semantics.get_page_state()
# PageState(
#   url="https://chat.deepseek.com/a/chat/s/...",
#   is_initial_page=False,
#   has_input=True,
#   is_streaming=True,        # model is generating
#   message_count=3,
#   deep_thinking_enabled=True,
#   web_search_enabled=False,
# )
```

---

## Semantic Capabilities Reference

### Session Management

| Action | Method | Description |
|--------|--------|-------------|
| New conversation | `chat.new_conversation()` | Click "开启新对话" button |
| List history | `chat.list_conversations()` | Extract sidebar conversation titles |
| Select history | `chat.select_conversation(title)` | Navigate to a past conversation |

### Mode & Input Controls

| Action | Method | Description |
|--------|--------|-------------|
| Select mode | `chat.select_mode(ChatMode.EXPERT)` | Choose "快速模式" or "专家模式" |
| Toggle deep thinking | `chat.toggle_deep_thinking()` | Enable/disable reasoning chain |
| Toggle web search | `chat.toggle_web_search()` | Enable/disable 智能搜索 |
| Send message | `chat.send_message(text)` | Type and submit to chat input |
| Upload file | `chat.upload_file(path)` | Trigger file upload dialog |

### Response Actions

Every assistant response has 5 action buttons. The model automatically maps their accessibility tree references:

| Action | Method | Description |
|--------|--------|-------------|
| Copy | `chat.copy_last_response()` | Copy response to clipboard |
| Regenerate | `chat.regenerate_last_response()` | Retry the last turn |
| Like | `chat.like_last_response()` | Thumbs up feedback |
| Dislike | `chat.dislike_last_response()` | Thumbs down feedback |
| Share | `chat.share_last_response()` | Open share dialog |

### Content Extraction

| Action | Method | Returns |
|--------|--------|---------|
| Get messages | `chat.get_messages()` | `[Message(role="user"\|"assistant", content="...")]` |
| Get thinking | `chat.get_thinking_trace()` | `ThinkingTrace(content="...", time="1 秒")` |
| Get actions | `chat.semantics.get_last_response_actions()` | `ResponseAction(copy_ref, regenerate_ref, ...)` |

### Waiting

| Action | Method | Description |
|--------|--------|-------------|
| Wait for response | `chat.wait_for_response(timeout=60)` | Polls with streaming-aware state machine |
| Wait for ready | `chat.wait_for_page_ready(timeout=10)` | Wait until page is interactive |

---

## As an MCP Server

This CLI is designed to be wrapped as an MCP tool suite. Here is a minimal implementation:

```python
# deepseek_mcp_server.py
from mcp.server import Server
from deepseek_browser_cli.deepseek_model import DeepSeekChat, ChatMode

server = Server("deepseek-web")
chat = DeepSeekChat(session="mcp", auto_connect=True)

@server.tool()
async def send_message(text: str) -> str:
    """Send a message to DeepSeek and wait for the response."""
    chat.send_message(text)
    return chat.wait_for_response(timeout=120) or "No response"

@server.tool()
async def toggle_deep_thinking() -> str:
    """Toggle the Deep Thinking (reasoning chain) feature."""
    ok = chat.toggle_deep_thinking()
    return "Enabled" if ok else "Failed"

@server.tool()
async def get_thinking_trace() -> str:
    """Get the reasoning chain from the last assistant response."""
    trace = chat.get_thinking_trace()
    return trace.content if trace else "No thinking trace"

@server.tool()
async def copy_last_response() -> str:
    """Copy the last assistant response to clipboard."""
    chat.copy_last_response()
    return "Copied"

@server.tool()
async def new_conversation() -> str:
    """Start a new conversation."""
    chat.new_conversation()
    return "New conversation started"

@server.tool()
async def select_mode(mode: str) -> str:
    """Select chat mode: 'quick' or 'expert'."""
    chat.select_mode(ChatMode.EXPERT if mode == "expert" else ChatMode.QUICK)
    return f"Mode set to {mode}"
```

### MCP Tool Manifest

```json
{
  "tools": [
    {
      "name": "send_message",
      "description": "Send a message to DeepSeek chat and return the response",
      "inputSchema": {
        "type": "object",
        "properties": {
          "text": { "type": "string", "description": "Message text to send" }
        },
        "required": ["text"]
      }
    },
    {
      "name": "toggle_deep_thinking",
      "description": "Toggle Deep Thinking (reasoning chain) mode"
    },
    {
      "name": "get_thinking_trace",
      "description": "Extract the reasoning chain from the last response"
    },
    {
      "name": "copy_last_response",
      "description": "Copy the last assistant response to clipboard"
    },
    {
      "name": "new_conversation",
      "description": "Start a new conversation thread"
    },
    {
      "name": "select_mode",
      "description": "Select chat mode: quick or expert",
      "inputSchema": {
        "type": "object",
        "properties": {
          "mode": { "type": "string", "enum": ["quick", "expert"] }
        },
        "required": ["mode"]
      }
    }
  ]
}
```

---

## As a Skill

For Claude Code or similar agent frameworks, expose the semantic actions as skills:

```python
# skills/deepseek_skill.py
from deepseek_browser_cli.deepseek_model import DeepSeekChat, ChatMode

class DeepSeekSkill:
    """DeepSeek web chat automation skill."""

    def __init__(self):
        self.chat = DeepSeekChat(session="skill", auto_connect=True)

    def ask(self, question: str, mode: str = "expert", deep_think: bool = True) -> str:
        """Ask DeepSeek a question and return the answer."""
        self.chat.goto("/")
        self.chat.select_mode(ChatMode.EXPERT if mode == "expert" else ChatMode.QUICK)

        state = self.chat.semantics.get_page_state()
        if deep_think and not state.deep_thinking_enabled:
            self.chat.toggle_deep_thinking()

        self.chat.send_message(question)
        return self.chat.wait_for_response(timeout=120) or "No response"

    def get_reasoning(self) -> str:
        """Get the reasoning chain from the last response."""
        trace = self.chat.get_thinking_trace()
        return trace.content if trace else "No reasoning available"
```

---

## Resilience Design

Every semantic action implements **3 fallback strategies**:

1. **Accessibility tree reference** (`@ref`) — fastest, most reliable
2. **CSS selector / DOM query** — survives minor UI changes
3. **JavaScript DOM manipulation** — bypasses all accessibility issues

Example: `send_message()` tries ref-based filling first, falls back to `document.querySelector('textarea').value = ...` if the a11y tree shifts.

---

## Data Models

```python
class ChatMode(str, Enum):
    QUICK = "快速模式"
    EXPERT = "专家模式"

@dataclass
class Message:
    role: str       # "user" | "assistant"
    content: str

@dataclass
class ThinkingTrace:
    content: str
    time: Optional[str]   # e.g. "1 秒"

@dataclass
class ResponseAction:
    copy_ref: Optional[str]
    regenerate_ref: Optional[str]
    like_ref: Optional[str]
    dislike_ref: Optional[str]
    share_ref: Optional[str]

@dataclass
class PageState:
    url: str
    is_initial_page: bool
    has_input: bool
    is_streaming: bool
    message_count: int
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

- **File upload**: Triggers the upload dialog but cannot complete file selection without OS-level automation or CDP's `DOM.setFileInputFiles`
- **Login/verification**: No automated handling for captcha or login flows
- **UI redesigns**: Class-name-based JS fallbacks may break on major UI updates
- **Session persistence**: Requires `agent-browser --session` to maintain browser context
