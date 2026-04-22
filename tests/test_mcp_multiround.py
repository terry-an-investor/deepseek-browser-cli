#!/usr/bin/env python3
"""Stress test: 20 rounds via MCP server."""

import asyncio
import json
import os
import shutil
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROMPTS = [
    "what model are you?",
    "what is 2+2?",
    "tell me a joke",
    "what is the capital of France?",
    "explain quantum computing in one sentence",
    "who invented the telephone?",
    "what is the speed of light?",
    "how many continents are there?",
    "what is the largest planet?",
    "who wrote Romeo and Juliet?",
    "what is the boiling point of water?",
    "what year did World War II end?",
    "what is the chemical symbol for gold?",
    "how many bones in the human body?",
    "what is the tallest mountain?",
    "who painted the Mona Lisa?",
    "what is the smallest prime number?",
    "what language is spoken in Brazil?",
    "what is the freezing point of water?",
    "who was the first president of the United States?",
]


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DEEPSEEK_E2E") != "1" or shutil.which("agent-browser") is None,
    reason="Requires RUN_DEEPSEEK_E2E=1 and agent-browser on PATH.",
)


@pytest.mark.asyncio
async def test_mcp_multiround():
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "deepseek_browser_cli.mcp_server"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            issues = []
            responses = []
            round_times = []

            for i, prompt in enumerate(PROMPTS, 1):
                start = time.time()
                print(f"\n{'='*60}")
                print(f"Round {i}/20: {prompt}")
                print("=" * 60)

                result = await session.call_tool(
                    "deepseek_chat", arguments={"message": prompt}
                )
                data = json.loads(result.content[0].text)

                elapsed = time.time() - start
                round_times.append(elapsed)

                if not data.get("success"):
                    issues.append(f"Round {i}: failed - {data.get('error')}")
                    print(f"  [ERROR] {data.get('error')}")
                    continue

                preview = data["response"][:80] if data["response"] else "(empty)"
                responses.append(data["response"])
                print(f"  Time: {elapsed:.1f}s")
                print(f"  Response: {preview}...")
                if "thinking" in data:
                    print(f"  Thinking: {data['thinking'].get('time')}")

            print(f"\n{'='*60}")
            print("SUMMARY")
            print("=" * 60)
            print(f"Rounds with response: {len(responses)}/20")
            print(f"Avg response time: {sum(round_times)/len(round_times):.1f}s")
            print(f"Issues: {len(issues)}")
            for issue in issues:
                print(f"  - {issue}")

            if not issues:
                print("\nAll 20 rounds passed via MCP!")
            else:
                print(f"\nCompleted with {len(issues)} issue(s).")
            assert not issues, "\n".join(issues)


if __name__ == "__main__":
    asyncio.run(test_mcp_multiround())
