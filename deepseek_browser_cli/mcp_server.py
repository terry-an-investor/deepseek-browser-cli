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
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

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


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def deepseek_chat(message: str, ctx: Context) -> str:
    """Send a message to DeepSeek and return the assistant's response.

    This is a convenience tool that handles the full turn:
    observe -> send -> wait for streaming -> extract response.

    Args:
        message: The message to send.

    Returns:
        JSON string with {"response": "...", "thinking": {...}, "success": true}
    """
    bridge = _get_bridge(ctx)

    # 1. Observe and wait until ready
    max_wait = 30
    waited = 0
    obs = bridge.observe()
    while waited < max_wait:
        state = obs["page_state"]
        if bridge._is_ready(state):
            break
        await asyncio.sleep(1)
        waited += 1
        obs = bridge.observe()

    if waited >= max_wait:
        return json.dumps(
            {"success": False, "error": "Timeout waiting for page to be ready"},
            ensure_ascii=False,
        )

    baseline = bridge._response_marker(obs)

    # 2. Send message
    send_result = bridge.act({"type": "send", "params": {"text": message}})
    if not send_result.get("success"):
        return json.dumps(
            {"success": False, "error": "Failed to send message"},
            ensure_ascii=False,
        )

    # 3. Wait for response (streaming to finish) with retry logic
    # DeepSeek is free and sometimes gets stuck; nudge after 10s of no progress
    max_wait = 120
    waited = 0
    stuck_counter = 0
    last_message_count = baseline["message_count"]
    while waited < max_wait:
        obs = bridge.observe()
        state = obs["page_state"]
        if bridge._has_new_response(baseline, obs) and bridge._is_ready(state):
            break

        # Detect stuck state: no streaming but also no new messages for 10+ seconds
        if state["is_streaming"]:
            stuck_counter = 0
        else:
            current_count = state["message_count"]
            if current_count == last_message_count:
                stuck_counter += 1
                if stuck_counter >= 10:
                    # Page might be stuck, try pressing Enter to nudge
                    bridge.act({"type": "press", "params": {"key": "Enter"}})
                    stuck_counter = 0
            else:
                last_message_count = current_count
                stuck_counter = 0

        await asyncio.sleep(1)
        waited += 1

    if waited >= max_wait:
        return json.dumps(
            {"success": False, "error": "Timeout waiting for response"},
            ensure_ascii=False,
        )

    # Let DOM settle after streaming stops (thinking trace rendering)
    await asyncio.sleep(1)

    # 4. Extract response
    obs = bridge.observe()
    if not bridge._has_new_response(baseline, obs):
        return json.dumps(
            {"success": False, "error": "No new response detected"},
            ensure_ascii=False,
        )
    last = obs.get("last_response", {})
    if not last.get("exists"):
        return json.dumps(
            {"success": False, "error": "No response detected"},
            ensure_ascii=False,
        )

    result = {
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
    result = bridge.act({"type": "new_chat", "params": {}})
    await asyncio.sleep(2)
    obs = bridge.observe()
    return json.dumps(
        {
            "success": result.get("success", False),
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
