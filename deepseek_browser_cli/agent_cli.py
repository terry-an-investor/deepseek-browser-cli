"""Agent CLI: JSON observation + action tools for AI agents.

This module provides CLI commands that output JSON observations
and accept JSON actions. The AI agent (you) reads the JSON,
reasons about it, and decides what to do.

Usage:
    $ deepseek-browser observe --session test
    {"page_state": {"can_send": true, ...}, ...}

    $ deepseek-browser act --session test --action '{"type": "send", "params": {"text": "hi"}}'
    {"success": true}
"""

import argparse
import json
import sys

from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge


def cmd_observe(args):
    """Observe the page and output JSON."""
    bridge = DeepSeekAgentBridge(session=args.session, auto_connect=args.auto_connect)
    obs = bridge.observe()
    print(json.dumps(obs, ensure_ascii=False, indent=2 if args.pretty else None))


def cmd_act(args):
    """Execute an action and output result JSON."""
    bridge = DeepSeekAgentBridge(session=args.session, auto_connect=args.auto_connect)
    action = json.loads(args.action)
    result = bridge.act(action)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


def cmd_turn(args):
    """Run one complete turn: send message, wait, output observation."""
    bridge = DeepSeekAgentBridge(session=args.session, auto_connect=args.auto_connect)
    result = bridge.run_turn(args.message, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


def main():
    parser = argparse.ArgumentParser(prog="deepseek-browser")
    parser.add_argument("--session", "-s", default="default", help="Session name")
    parser.add_argument("--auto-connect", "-a", action="store_true", help="Connect to running Chrome")
    parser.add_argument("--pretty", "-p", action="store_true", help="Pretty-print JSON output")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # observe
    observe_parser = subparsers.add_parser("observe", help="Observe page state as JSON")
    observe_parser.set_defaults(func=cmd_observe)

    # act
    act_parser = subparsers.add_parser("act", help="Execute an action from JSON")
    act_parser.add_argument("--action", "-a", required=True, help="JSON action object")
    act_parser.set_defaults(func=cmd_act)

    # turn
    turn_parser = subparsers.add_parser("turn", help="Send message and get observation")
    turn_parser.add_argument("message", help="Message to send")
    turn_parser.add_argument("--timeout", "-t", type=float, default=120, help="Timeout in seconds")
    turn_parser.set_defaults(func=cmd_turn)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
