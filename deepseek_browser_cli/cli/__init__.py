"""Simple CLI wrapper for agent-browser."""

import argparse
import re
import subprocess
import sys
import os
import time


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


def interactive_session(session, auto_connect=False):
    """Run an interactive multi-round conversation session."""
    from deepseek_browser_cli.deepseek_model import DeepSeekChat

    chat = DeepSeekChat(session=session, auto_connect=auto_connect)

    print("Multi-round chat session. Type 'quit' to exit.")
    print("=" * 50)

    while True:
        try:
            print("\n> ", end="", flush=True)
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if line.lower() in ('quit', 'exit', 'q'):
            print("Goodbye!")
            break

        if not line:
            continue

        if not _send_message_impl(session, line, auto_connect):
            continue

        read_response(chat)


def _clean_env():
    """Return environment without proxy variables."""
    return {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="deepseek-browser")
    parser.add_argument("--session", "-s", default="default", help="Session name")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--auto-connect", "-a", action="store_true", help="Connect to running Chrome")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive multi-round chat")
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

        interactive_session(args.session, args.auto_connect)
        return 0

    if not args.command:
        parser.print_help()
        return 0

    cmd.extend(args.command)
    result = subprocess.run(cmd, env=_clean_env())
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
