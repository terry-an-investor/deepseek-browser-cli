#!/usr/bin/env python3
"""Test the deepseek_mode MCP tool."""

import asyncio
import json
import os
import shutil

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DEEPSEEK_E2E") != "1" or shutil.which("agent-browser") is None,
    reason="Requires RUN_DEEPSEEK_E2E=1 and agent-browser on PATH.",
)


@pytest.mark.asyncio
async def test_mcp_mode_flow():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "deepseek_browser_cli.mcp_server"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("--- Start fresh conversation ---")
            result = await session.call_tool("deepseek_new_chat", arguments={})
            data = json.loads(result.content[0].text)
            print(f"New chat: {data['success']}")
            assert data["success"] is True, data

            print("\n--- Observe initial state ---")
            result = await session.call_tool("deepseek_observe", arguments={})
            obs = json.loads(result.content[0].text)
            print(f"Is initial page: {obs['page_state']['is_initial_page']}")
            print(f"Initial mode: {obs['page_state']['mode']}")
            assert obs["page_state"]["is_initial_page"] is True

            print("\n--- Switch to expert mode ---")
            result = await session.call_tool(
                "deepseek_mode", arguments={"mode": "expert"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            print(f"Mode now: {data['mode']}")
            assert data["success"] is True, data
            assert data["mode"] == "expert"

            print("\n--- Send a message in expert mode ---")
            result = await session.call_tool(
                "deepseek_chat", arguments={"message": "what model are you?"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            assert data["success"] is True, data
            if data.get('success'):
                print(f"Response: {data['response'][:80]}...")
            else:
                print(f"Error: {data.get('error')}")

            print("\n--- Try switching mode mid-conversation (should fail) ---")
            result = await session.call_tool(
                "deepseek_mode", arguments={"mode": "instant"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            print(f"Error: {data.get('error')}")
            assert data["success"] is False, data

            print("\n--- Start new chat and switch to instant ---")
            result = await session.call_tool("deepseek_new_chat", arguments={})
            data = json.loads(result.content[0].text)
            print(f"New chat: {data['success']}")
            assert data["success"] is True, data

            result = await session.call_tool(
                "deepseek_mode", arguments={"mode": "instant"}
            )
            data = json.loads(result.content[0].text)
            print(f"Mode switch: {data['success']}, mode={data['mode']}")
            assert data["success"] is True, data
            assert data["mode"] == "instant"

            print("\n--- Send a message in instant mode ---")
            result = await session.call_tool(
                "deepseek_chat", arguments={"message": "what is 2+2?"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            assert data["success"] is True, data
            if data.get('success'):
                print(f"Response: {data['response'][:80]}...")
            else:
                print(f"Error: {data.get('error')}")

            print("\n--- Test invalid mode ---")
            result = await session.call_tool(
                "deepseek_mode", arguments={"mode": "invalid"}
            )
            data = json.loads(result.content[0].text)
            print(f"Success: {data['success']}")
            print(f"Error: {data.get('error')}")
            assert data["success"] is False, data

            print("\n=== Mode switch test passed ===")


if __name__ == "__main__":
    asyncio.run(test_mcp_mode_flow())
