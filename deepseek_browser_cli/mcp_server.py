"""MCP server for DeepSeek web chat.

Exposes tools for agents to interact with chat.deepseek.com via browser automation.

Tools:
  - deepseek_chat: send a message and get the response (convenience)
  - deepseek_observe: inspect current page state
  - deepseek_toggle: toggle thinking/search on or off
  - deepseek_mode: switch between quick and expert mode
  - deepseek_new_chat: start a fresh conversation
"""

import asyncio
import ast
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from mcp.server.fastmcp import Context, FastMCP

from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge


# ---------------------------------------------------------------------------
# Lifespan: one browser session for the whole MCP server lifetime
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Start the browser session when the MCP server starts."""
    bridge = DeepSeekAgentBridge(session="mcp-server", auto_connect=True)
    # Navigate to DeepSeek
    bridge.act({"type": "eval", "params": {"script": "window.location.href = 'https://chat.deepseek.com'"}})
    await asyncio.sleep(2)
    yield {"bridge": bridge}


mcp = FastMCP(
    "deepseek-web",
    instructions="""Control the DeepSeek web chat via browser automation.

Use `deepseek_chat` for normal back-and-forth conversation.
Use `deepseek_observe` to check state (streaming, can_send, etc).
Use `deepseek_toggle` to enable/disable deep thinking or web search.
Use `deepseek_mode` to switch between instant and expert mode (only on a new dialog, before starting a conversation).
Use `deepseek_new_chat` to start a fresh conversation thread.
""",
    lifespan=lifespan,
)


def _get_bridge(ctx: Context) -> DeepSeekAgentBridge:
    """Extract bridge from MCP request context."""
    return ctx.request_context.lifespan_context["bridge"]


def _parse_eval_json(raw: str) -> Optional[dict[str, Any]]:
    """Parse JSON payloads returned by agent-browser eval."""
    if not raw:
        return None

    text = raw.strip()

    for candidate in (text,):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    try:
        literal = ast.literal_eval(text)
        if isinstance(literal, dict):
            return literal
        if isinstance(literal, str):
            parsed = json.loads(literal)
            if isinstance(parsed, dict):
                return parsed
    except (ValueError, SyntaxError, json.JSONDecodeError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


def _observe_chat_fast(bridge: DeepSeekAgentBridge) -> Optional[dict[str, Any]]:
    """Get a lightweight chat snapshot with one JS eval call."""
    js = r"""
    (function() {
        const out = {
            has_input: false,
            is_streaming: false,
            can_send: false,
            message_count: 0,
            assistant_count: 0,
            last_response: {
                exists: false,
                content: "",
                thinking: { exists: false, time: null, content: null }
            }
        };

        const textarea = document.querySelector('textarea');
        out.has_input = !!textarea;

        const bodyText = document.body ? (document.body.innerText || '') : '';
        const spinner = document.querySelector(
            '.ds-loading, [class*="loading"], [class*="spinner"], [aria-busy="true"]'
        );
        out.is_streaming = !!spinner || /正在思考|generating|thinking/i.test(bodyText);
        out.can_send = out.has_input && !out.is_streaming;

        const messageNodes = Array.from(document.querySelectorAll('.ds-message'));
        out.message_count = messageNodes.length;

        let lastAssistant = null;
        for (let i = messageNodes.length - 1; i >= 0; i--) {
            const classes = (messageNodes[i].className || '').split(/\s+/).filter(Boolean);
            if (classes[0] === 'ds-message') {
                out.assistant_count += 1;
                if (!lastAssistant) {
                    lastAssistant = messageNodes[i];
                }
            }
        }

        if (lastAssistant) {
            out.last_response.exists = true;
            const markdowns = lastAssistant.querySelectorAll('.ds-markdown');
            let content = '';
            for (const md of markdowns) {
                if (!md.closest('.ds-think-content')) {
                    content = (md.textContent || '').trim();
                }
            }
            if (!content) {
                content = (lastAssistant.textContent || '').trim();
            }
            content = content.replace(/^(正在思考\s*)+/, '');
            content = content.replace(/^已思考[（(]用时\s*[^）)]+[）)]\s*/, '');
            out.last_response.content = content.substring(0, 8000);

            const thinkHead = lastAssistant.querySelector('.ds-think-head');
            const thinkContent = lastAssistant.querySelector('.ds-think-content');
            if (thinkHead || thinkContent) {
                out.last_response.thinking.exists = true;
                const headText = thinkHead ? (thinkHead.textContent || '') : '';
                const match = headText.match(/用时\s*([^）)]+)/);
                out.last_response.thinking.time = match ? match[1].trim() : null;
                out.last_response.thinking.content = thinkContent
                    ? (thinkContent.textContent || '').trim().substring(0, 8000)
                    : null;
            }
        }

        return JSON.stringify(out);
    })()
    """
    result = bridge.act({"type": "eval", "params": {"script": js}})
    if not result.get("success"):
        return None
    return _parse_eval_json(result.get("output", ""))


def _has_new_response_fast(baseline: dict[str, Any], current: dict[str, Any]) -> bool:
    """Detect whether a fresh assistant response appears after baseline."""
    baseline_last = baseline.get("last_response", {})
    current_last = current.get("last_response", {})
    return (
        current.get("assistant_count", 0) > baseline.get("assistant_count", 0)
        or (
            bool(current_last.get("content"))
            and current_last.get("content") != baseline_last.get("content")
        )
    )


def _chat_result_from_obs(obs: dict[str, Any]) -> str:
    """Build stable tool output from a chat observation."""
    last = obs.get("last_response", {})
    result: dict[str, Any] = {
        "success": True,
        "response": last.get("content", ""),
    }
    thinking = last.get("thinking", {})
    if thinking.get("exists"):
        result["thinking"] = {
            "time": thinking.get("time"),
            "content": thinking.get("content"),
        }
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def deepseek_chat(
    message: str,
    ctx: Context,
    timeout: float = 120,
    poll_interval: float = 0.25,
) -> str:
    """Send a message to DeepSeek and return the assistant's response.

    This is a convenience tool that handles the full turn:
    observe -> send -> wait for streaming -> extract response.

    Args:
        message: The message to send.
        timeout: Maximum seconds to wait for completion.
        poll_interval: Poll cadence in seconds (lower = faster but more CPU).

    Returns:
        JSON string with {"response": "...", "thinking": {...}, "success": true}
    """
    bridge = _get_bridge(ctx)
    poll_interval = max(0.1, min(1.0, poll_interval))

    # 1. Observe and wait until ready (fast polling)
    ready_deadline = time.monotonic() + 30
    obs = _observe_chat_fast(bridge)
    while time.monotonic() < ready_deadline:
        if obs and obs.get("can_send"):
            break
        await asyncio.sleep(poll_interval)
        obs = _observe_chat_fast(bridge)

    if not obs or not obs.get("can_send"):
        return json.dumps(
            {"success": False, "error": "Timeout waiting for page to be ready"},
            ensure_ascii=False,
        )

    baseline = obs

    # 2. Send message
    send_result = bridge.act({"type": "send", "params": {"text": message}})
    if not send_result.get("success"):
        return json.dumps(
            {"success": False, "error": "Failed to send message"},
            ensure_ascii=False,
        )

    # 3. Wait for response with low-latency loop.
    deadline = time.monotonic() + timeout
    response_started = False
    idle_since = time.monotonic()
    last_streamed = ""

    while time.monotonic() < deadline:
        obs = _observe_chat_fast(bridge)
        if not obs:
            await asyncio.sleep(poll_interval)
            continue

        has_new = _has_new_response_fast(baseline, obs)
        if has_new:
            response_started = True
            idle_since = time.monotonic()
            current = obs.get("last_response", {}).get("content", "")
            if current and len(current) > len(last_streamed):
                delta = current[len(last_streamed) :]
                if delta.strip():
                    await ctx.info(delta)
                last_streamed = current

        if response_started and obs.get("can_send"):
            # Small settle check to avoid truncation without adding 1s fixed latency.
            await asyncio.sleep(min(0.2, poll_interval))
            settled = _observe_chat_fast(bridge)
            if settled and settled.get("can_send") and _has_new_response_fast(baseline, settled):
                return _chat_result_from_obs(settled)

        # DeepSeek sometimes stalls; nudge if nothing changes for 10s after streaming started.
        if (
            response_started
            and not obs.get("is_streaming")
            and (time.monotonic() - idle_since) >= 10
        ):
            bridge.act({"type": "press", "params": {"key": "Enter"}})
            idle_since = time.monotonic()

        await asyncio.sleep(poll_interval)

    obs = _observe_chat_fast(bridge)
    if obs and _has_new_response_fast(baseline, obs) and obs.get("last_response", {}).get("exists"):
        return _chat_result_from_obs(obs)

    partial = None
    if obs:
        partial = obs.get("last_response", {}).get("content")
    if partial:
        return json.dumps(
            {"success": False, "error": "Timeout waiting for response", "partial_response": partial},
            ensure_ascii=False,
        )
    return json.dumps(
        {"success": False, "error": "Timeout waiting for response"},
        ensure_ascii=False,
    )


@mcp.tool()
def deepseek_observe(ctx: Context) -> str:
    """Observe the current state of the DeepSeek chat page.

    Returns:
        JSON with page_state, input_area, messages, last_response, sidebar.
    """
    bridge = _get_bridge(ctx)
    obs = bridge.observe()
    return json.dumps(obs, ensure_ascii=False, indent=2)


@mcp.tool()
def deepseek_toggle(feature: str, ctx: Context) -> str:
    """Toggle a feature on/off.

    Args:
        feature: Either "deep_thinking" or "web_search".

    Returns:
        JSON with success status and new state.
    """
    bridge = _get_bridge(ctx)
    if feature not in ("deep_thinking", "web_search"):
        return json.dumps(
            {"success": False, "error": f"Unknown feature: {feature}. Use 'deep_thinking' or 'web_search'."},
            ensure_ascii=False,
        )

    result = bridge.act({"type": "toggle", "params": {"feature": feature}})
    obs = bridge.observe()
    state = obs["page_state"]
    return json.dumps(
        {
            "success": result.get("success", False),
            "deep_thinking_enabled": state.get("deep_thinking_enabled"),
            "web_search_enabled": state.get("web_search_enabled"),
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def deepseek_mode(mode: str, ctx: Context) -> str:
    """Select the chat mode. Only works on a new dialog (landing page).

    Mode cannot be changed mid-conversation. If you need to switch modes,
    call deepseek_new_chat() first to return to the landing page.

    Args:
        mode: Either "expert" or "instant".

    Returns:
        JSON with success status and current mode.
    """
    bridge = _get_bridge(ctx)
    # Accept both "instant" and "quick" (canonical internal name)
    canonical = mode
    if mode == "quick":
        canonical = "instant"
    if canonical not in ("expert", "instant"):
        return json.dumps(
            {"success": False, "error": f"Unknown mode: {mode}. Use 'expert' or 'instant'."},
            ensure_ascii=False,
        )

    obs = bridge.observe()
    if not obs["page_state"]["is_initial_page"]:
        return json.dumps(
            {
                "success": False,
                "error": "Mode can only be switched on a new dialog (before starting a conversation). Call deepseek_new_chat() first.",
                "mode": obs["page_state"]["mode"],
            },
            ensure_ascii=False,
        )

    result = bridge.act({"type": "mode", "params": {"mode": mode}})
    await asyncio.sleep(0.5)  # Let DOM update after mode switch
    obs = bridge.observe()
    state = obs["page_state"]
    return json.dumps(
        {
            "success": result.get("success", False),
            "mode": state.get("mode"),
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def deepseek_new_chat(ctx: Context) -> str:
    """Start a new conversation thread.

    Returns:
        JSON with success status.
    """
    bridge = _get_bridge(ctx)
    obs = bridge.observe()
    if obs["page_state"]["is_initial_page"]:
        return json.dumps(
            {
                "success": True,
                "url": obs["page_state"]["url"],
                "is_initial_page": True,
            },
            ensure_ascii=False,
        )

    result = bridge.act({"type": "new_chat", "params": {}})

    max_wait = 10
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        await asyncio.sleep(0.25)
        obs = bridge.observe()
        if obs["page_state"]["is_initial_page"]:
            break

    obs = bridge.observe()
    return json.dumps(
        {
            "success": result.get("success", False) and obs.get("page_state", {}).get("is_initial_page", False),
            "url": obs.get("page_state", {}).get("url"),
            "is_initial_page": obs.get("page_state", {}).get("is_initial_page"),
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
