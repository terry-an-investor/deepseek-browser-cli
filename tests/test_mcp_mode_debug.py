#!/usr/bin/env python3
"""Debug mode detection."""

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
async def test_mcp_mode_debug_flow():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "deepseek_browser_cli.mcp_server"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Start fresh
            await session.call_tool("deepseek_new_chat", arguments={})

            # Observe before mode switch
            result = await session.call_tool("deepseek_observe", arguments={})
            obs = json.loads(result.content[0].text)
            print(f"Before mode switch: is_initial={obs['page_state']['is_initial_page']}, mode={obs['page_state']['mode']}")
            assert obs["page_state"]["is_initial_page"] is True

            # Switch to expert
            result = await session.call_tool("deepseek_mode", arguments={"mode": "expert"})
            data = json.loads(result.content[0].text)
            print(f"Mode switch result: success={data['success']}")
            assert data["success"] is True, data

            # Observe after mode switch
            result = await session.call_tool("deepseek_observe", arguments={})
            obs = json.loads(result.content[0].text)
            print(f"After mode switch: is_initial={obs['page_state']['is_initial_page']}, mode={obs['page_state']['mode']}")
            assert obs["page_state"]["mode"] == "expert"

            # Send a message
            result = await session.call_tool("deepseek_chat", arguments={"message": "what model are you?"})
            data = json.loads(result.content[0].text)
            print(f"Chat result: success={data['success']}")
            assert data["success"] is True, data
            if data.get('success'):
                print(f"Response: {data['response'][:100]}...")
            else:
                print(f"Error: {data.get('error')}")

            # Observe after chat
            result = await session.call_tool("deepseek_observe", arguments={})
            obs = json.loads(result.content[0].text)
            print(f"After chat: mode={obs['page_state']['mode']}")
            assert obs["page_state"]["mode"] == "expert"


if __name__ == "__main__":
    asyncio.run(test_mcp_mode_debug_flow())
