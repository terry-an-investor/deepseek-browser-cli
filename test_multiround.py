#!/usr/bin/env python3
"""Stress test: 20-round multi-round chat via agent bridge.

This test verifies that the bridge can maintain a stable conversation
across many rounds. With DeepSeek's virtual scrolling, message counts
are approximate for long conversations — we focus on whether each
send succeeds and produces a non-empty response.
"""

import time
from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge

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


def main():
    bridge = DeepSeekAgentBridge(session="multiround-test", auto_connect=True)

    # Start fresh
    print("[Starting new conversation...]")
    result = bridge.act(
        {"type": "eval", "params": {"script": "window.location.href = 'https://chat.deepseek.com'"}}
    )
    time.sleep(2)

    issues = []
    round_times = []
    responses = []

    for i, prompt in enumerate(PROMPTS, 1):
        start = time.time()
        print(f"\n{'='*60}")
        print(f"Round {i}/20: {prompt}")
        print("=" * 60)

        # Send message
        result = bridge.act({"type": "send", "params": {"text": prompt}})
        if not result.get("success"):
            issues.append(f"Round {i}: send failed")
            print(f"  [ERROR] Send failed")
            continue

        # Wait for streaming to complete
        max_wait = 60
        waited = 0
        while waited < max_wait:
            obs = bridge.observe()
            state = obs["page_state"]
            if not state["is_streaming"] and state["can_send"]:
                break
            time.sleep(1)
            waited += 1

        elapsed = time.time() - start
        round_times.append(elapsed)

        if waited >= max_wait:
            issues.append(f"Round {i}: timeout waiting for response")
            print(f"  [ERROR] Timeout after {max_wait}s")
            continue

        # Verify state
        obs = bridge.observe()
        state = obs["page_state"]
        visible_count = state["message_count"]

        print(f"  Visible messages: {visible_count}")
        print(f"  Streaming: {state['is_streaming']}")
        print(f"  Can send: {state['can_send']}")
        print(f"  Time: {elapsed:.1f}s")

        last = obs["last_response"]
        if last["exists"] and last["content"]:
            preview = last["content"][:80]
            responses.append(last["content"])
            print(f"  Response: {preview}...")
            if last["thinking"]["exists"]:
                print(f"  Thinking: {last['thinking']['time']}")
        else:
            issues.append(f"Round {i}: no response detected")
            print(f"  [ERROR] No response detected")

    print(f"\n{'='*60}")
    print("SUMMARY")
    print("=" * 60)
    print(f"Rounds with response: {len(responses)}/20")
    print(
        f"Avg response time: {sum(round_times)/len(round_times):.1f}s"
        if round_times
        else "N/A"
    )
    print(f"Issues: {len(issues)}")
    for issue in issues:
        print(f"  - {issue}")

    if not issues:
        print("\nAll 20 rounds completed successfully!")
    else:
        print(f"\nTest completed with {len(issues)} issue(s).")


if __name__ == "__main__":
    main()
