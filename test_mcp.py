#!/usr/bin/env python3
"""Test the DeepSeek MCP server end-to-end via stdio transport."""

import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_mcp_server():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "deepseek_browser_cli.mcp_server"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List tools
            tools = await session.list_tools()
            print(f"Tools: {[t.name for t in tools.tools]}")
            assert len(tools.tools) == 4, f"Expected 4 tools, got {len(tools.tools)}"

            # Test observe
            print("\n--- Testing deepseek_observe ---")
            result = await session.call_tool("deepseek_observe", arguments={})
            obs = json.loads(result.content[0].text)
            print(f"URL: {obs['page_state']['url']}")
            print(f"Can send: {obs['page_state']['can_send']}")
            print(f"Streaming: {obs['page_state']['is_streaming']}")

            # Test chat - round 1
            print("\n--- Testing deepseek_chat (round 1) ---")
            result = await session.call_tool(
                "deepseek_chat", arguments={"message": "what model are you?"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            print(f"Response: {data['response'][:80]}...")

            # Test chat - round 2 (same conversation)
            print("\n--- Testing deepseek_chat (round 2) ---")
            result = await session.call_tool(
                "deepseek_chat", arguments={"message": "what is 2+2?"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            print(f"Response: {data['response'][:80]}...")

            # Test chat - round 3
            print("\n--- Testing deepseek_chat (round 3) ---")
            result = await session.call_tool(
                "deepseek_chat", arguments={"message": "tell me a joke"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            print(f"Response: {data['response'][:80]}...")

            # Test toggle
            print("\n--- Testing deepseek_toggle ---")
            result = await session.call_tool(
                "deepseek_toggle", arguments={"feature": "deep_thinking"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            print(f"Deep thinking: {data.get('deep_thinking_enabled')}")

            # Test new chat
            print("\n--- Testing deepseek_new_chat ---")
            result = await session.call_tool("deepseek_new_chat", arguments={})
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")

            # Observe after new chat
            print("\n--- Observing after new chat ---")
            result = await session.call_tool("deepseek_observe", arguments={})
            obs = json.loads(result.content[0].text)
            print(f"Message count: {obs['page_state']['message_count']}")

            print("\n=== All tests passed ===")


if __name__ == "__main__":
    asyncio.run(test_mcp_server())
