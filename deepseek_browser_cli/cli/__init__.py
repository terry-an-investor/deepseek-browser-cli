"""Simple CLI wrapper for agent-browser."""

import argparse
import re
import shutil
import subprocess
import sys
import time

from deepseek_browser_cli.primitives import _clean_env


def _ensure_agent_browser_available() -> None:
    if shutil.which("agent-browser") is None:
        raise RuntimeError(
            "agent-browser was not found on PATH. "
            "Install it with: npm install -g agent-browser"
        )


def _find_textbox_selector(session, auto_connect=False):
    """Find the textbox selector for the current page.

    Returns a CSS selector string (e.g. 'textarea', 'input[type=text]')
    that can be used directly with agent-browser commands.  Using CSS
    selectors avoids SIGTRAP crashes observed with @ref on macOS.
    """
    cmd = ["agent-browser", "--session", session]
    if auto_connect:
        cmd.append("--auto-connect")
    cmd.append("snapshot")

    result = subprocess.run(cmd, capture_output=True, text=True, env=_clean_env())
    snapshot = result.stdout

    # Strategy 1: Look for textarea (most common for chat inputs)
    if "textarea" in snapshot:
        return "textarea"

    # Strategy 2: Look for textbox with ref and build a fallback selector
    for line in snapshot.split("\n"):
        if 'textbox "' in line and "[ref=" in line:
            # Try to find an input by placeholder/label text
            match = re.search(r'textbox "([^"]+)"', line)
            if match:
                placeholder = match.group(1)
                return f'input[placeholder="{placeholder}"]'

    # Strategy 3: Generic text input fallback
    if "textbox" in snapshot:
        return "input[type=text]"

    return None


def _send_message_impl(session, line, auto_connect=False):
    """Send a single message using CSS selectors.

    Uses CSS selectors instead of @ref to avoid SIGTRAP crashes on macOS
    when Playwright interacts with Chromium via CDP.
    """
    selector = _find_textbox_selector(session, auto_connect)
    if not selector:
        print("Error: Could not find textbox on page")
        return False

    cmd = ["agent-browser", "--session", session]
    if auto_connect:
        cmd.append("--auto-connect")

    # Use fill instead of type + click for reliability
    result = subprocess.run(
        cmd + ["fill", selector, line],
        env=_clean_env(), capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error: Failed to fill textbox: {result.stderr.strip()}")
        return False

    result = subprocess.run(cmd + ["press", "Enter"], env=_clean_env(), capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: Failed to press Enter: {result.stderr.strip()}")
        return False

    return True


def read_response(chat, timeout=60):
    """Read response after sending message using semantic model."""
    print("[Waiting for response...]")

    response = chat.wait_for_response(timeout=timeout)
    if response:
        print(f"\n{response}")
    else:
        print("\n[No response received]")

    return response


def interactive_session(session, auto_connect=False, mode="expert", thinking=True, search=False):
    """Run an interactive multi-round conversation session.

    Supports:
    - Unlimited back-and-forth turns
    - /new to start a fresh conversation
    - /copy, /like, /dislike, /share to act on last response
    - /history to show conversation history
    - /export to save conversation as markdown
    - /mode quick|expert to switch mode
    - /thinking on|off to toggle deep thinking
    - /search on|off to toggle web search
    - /quit to exit
    """
    from deepseek_browser_cli.chat import MultiRoundChat
    from deepseek_browser_cli.models import ChatMode

    chat_mode = ChatMode.EXPERT if mode == "expert" else ChatMode.QUICK
    chat = MultiRoundChat(
        session=session,
        auto_connect=auto_connect,
        mode=chat_mode,
        enable_thinking=thinking,
        enable_search=search,
    )

    print("=" * 60)
    print("DeepSeek Multi-Round Chat")
    print("=" * 60)
    print(f"Mode: {mode} | Thinking: {'on' if thinking else 'off'} | Search: {'on' if search else 'off'}")
    print()
    print("Commands:")
    print("  /new        - Start a new conversation")
    print("  /copy       - Copy last response to clipboard")
    print("  /regen      - Regenerate last response")
    print("  /like       - Like last response")
    print("  /dislike    - Dislike last response")
    print("  /share      - Share last response")
    print("  /history    - Show conversation history")
    print("  /export     - Export conversation as markdown")
    print("  /mode q|e   - Switch to quick/expert mode")
    print("  /think on|off - Toggle deep thinking")
    print("  /search on|off - Toggle web search")
    print("  /quit       - Exit")
    print("=" * 60)
    print()

    # Initialize
    chat.setup()

    turn_count = 0
    while True:
        try:
            print(f"\n[{turn_count + 1}] > ", end="", flush=True)
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not line:
            continue

        # Slash commands
        if line.startswith("/"):
            cmd = line[1:].lower().split()
            if not cmd:
                continue

            action = cmd[0]

            if action in ("quit", "q", "exit"):
                print("Goodbye!")
                break

            elif action == "new":
                chat.new_conversation()
                turn_count = 0
                print("[New conversation started]")
                continue

            elif action == "copy":
                if chat.copy_last():
                    print("[Copied to clipboard]")
                else:
                    print("[Nothing to copy]")
                continue

            elif action == "regen":
                print("[Regenerating...]")
                turn = chat.regenerate_last()
                if turn:
                    print(f"\n{turn.assistant_response}")
                else:
                    print("[Failed to regenerate]")
                continue

            elif action == "like":
                if chat.like_last():
                    print("[Liked]")
                else:
                    print("[Nothing to like]")
                continue

            elif action == "dislike":
                if chat.dislike_last():
                    print("[Disliked]")
                else:
                    print("[Nothing to dislike]")
                continue

            elif action == "share":
                if chat.share_last():
                    print("[Share dialog opened]")
                else:
                    print("[Nothing to share]")
                continue

            elif action == "history":
                history = chat.get_history()
                if not history:
                    print("[No history yet]")
                    continue
                for i, turn in enumerate(history, 1):
                    user_preview = turn.user_message[:60]
                    if len(turn.user_message) > 60:
                        user_preview += "..."
                    print(f"\n  Turn {i}:")
                    print(f"    User: {user_preview}")
                    resp_preview = turn.assistant_response[:80]
                    if len(turn.assistant_response) > 80:
                        resp_preview += "..."
                    print(f"    Assistant: {resp_preview}")
                continue

            elif action == "export":
                md = chat.export_markdown()
                filename = f"deepseek_chat_{int(time.time())}.md"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(md)
                print(f"[Exported to {filename}]")
                continue

            elif action == "mode":
                if len(cmd) < 2:
                    print("[Usage: /mode quick|expert]")
                    continue
                new_mode = cmd[1]
                if new_mode in ("q", "quick"):
                    chat.mode = ChatMode.QUICK
                    chat._is_setup = False
                    chat.setup()
                    print("[Switched to quick mode]")
                elif new_mode in ("e", "expert"):
                    chat.mode = ChatMode.EXPERT
                    chat._is_setup = False
                    chat.setup()
                    print("[Switched to expert mode]")
                else:
                    print("[Usage: /mode quick|expert]")
                continue

            elif action in ("think", "thinking"):
                if len(cmd) < 2:
                    print(f"[Deep thinking: {'on' if chat.enable_thinking else 'off'}]")
                    continue
                val = cmd[1]
                if val == "on":
                    chat.enable_thinking = True
                    if chat.chat.toggle_deep_thinking():
                        print("[Deep thinking enabled]")
                elif val == "off":
                    chat.enable_thinking = False
                    if chat.chat.toggle_deep_thinking():
                        print("[Deep thinking disabled]")
                else:
                    print("[Usage: /think on|off]")
                continue

            elif action == "search":
                if len(cmd) < 2:
                    print(f"[Web search: {'on' if chat.enable_search else 'off'}]")
                    continue
                val = cmd[1]
                if val == "on":
                    chat.enable_search = True
                    if chat.chat.toggle_web_search():
                        print("[Web search enabled]")
                elif val == "off":
                    chat.enable_search = False
                    if chat.chat.toggle_web_search():
                        print("[Web search disabled]")
                else:
                    print("[Usage: /search on|off]")
                continue

            else:
                print(f"[Unknown command: /{action}]")
                continue

        # Regular message
        turn_count += 1
        print("[Sending...]")

        # Use streaming mode for better UX
        def on_token(delta, full):
            print(delta, end="", flush=True)

        turn = chat.send_streaming(line, timeout=120, on_token=on_token)

        if turn:
            # Print newline if streaming didn't end with one
            if not turn.assistant_response.endswith("\n"):
                print()

            if turn.thinking_trace:
                print(f"\n[Thought for {turn.thinking_trace.time}]")
        else:
            print("[Failed to get response]")


def main(argv=None):
    try:
        _ensure_agent_browser_available()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(prog="deepseek-browser")
    parser.add_argument("--session", "-s", default="default", help="Session name")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--auto-connect", "-a", action="store_true", help="Connect to running Chrome")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive multi-round chat")
    parser.add_argument("--mode", "-m", choices=["quick", "expert"], default="expert", help="Chat mode")
    parser.add_argument("--thinking", action="store_true", default=True, help="Enable deep thinking")
    parser.add_argument("--no-thinking", action="store_false", dest="thinking", help="Disable deep thinking")
    parser.add_argument("--search", action="store_true", default=False, help="Enable web search")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="agent-browser command and arguments")
    args = parser.parse_args(argv)

    cmd = ["agent-browser"]
    if args.session:
        cmd.extend(["--session", args.session])
    if args.headed:
        cmd.append("--headed")
    if args.auto_connect:
        cmd.append("--auto-connect")

    if args.interactive:
        if not args.auto_connect and not args.headed:
            subprocess.Popen(cmd + ["--no-close"], env=_clean_env())
            time.sleep(2)

        interactive_session(
            args.session,
            args.auto_connect,
            mode=args.mode,
            thinking=args.thinking,
            search=args.search,
        )
        return 0

    if not args.command:
        parser.print_help()
        return 0

    cmd.extend(args.command)
    result = subprocess.run(cmd, env=_clean_env())
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
