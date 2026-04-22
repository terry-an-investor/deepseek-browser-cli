#!/usr/bin/env python3
"""Regression tests for non-network behavior."""

from deepseek_browser_cli.agent_bridge import DeepSeekAgentBridge
from deepseek_browser_cli.deepseek_model import (
    DeepSeekChat,
    DeepSeekSemantics,
    Message,
    MultiRoundChat,
)


class FakeA11y:
    """Minimal a11y stub for semantic unit tests."""

    def __init__(self, snapshot: str, eval_results=None, url: str = "https://chat.deepseek.com"):
        self._snapshot = snapshot
        self._eval_results = list(eval_results or [])
        self._url = url
        self.eval_calls = 0

    def snapshot(self) -> str:
        return self._snapshot

    def get_url(self) -> str:
        return self._url

    def eval_js(self, script: str):
        self.eval_calls += 1
        if self._eval_results:
            return self._eval_results.pop(0)
        return ("", 1)


def test_get_page_state_does_not_treat_toggle_presence_as_enabled():
    semantics = DeepSeekSemantics(
        FakeA11y(
            snapshot=(
                '- button "深度思考" [ref=t1]\n'
                '- button "智能搜索" [ref=t2]\n'
                '- textbox "给 DeepSeek" [ref=box1]\n'
            ),
            eval_results=[("", 1)],
        )
    )

    state = semantics.get_page_state()

    assert state.deep_thinking_enabled is False
    assert state.web_search_enabled is False


def test_get_page_state_reads_pressed_toggle_from_snapshot_fallback():
    semantics = DeepSeekSemantics(
        FakeA11y(
            snapshot='- button "深度思考" [pressed] [ref=t1]\n',
            eval_results=[("", 1)],
        )
    )

    state = semantics.get_page_state()

    assert state.deep_thinking_enabled is True


def test_response_action_grouping_resets_between_sections():
    semantics = DeepSeekSemantics(
        FakeA11y(
            snapshot=(
                '  - button [ref=old1]\n'
                '  - button [ref=old2]\n'
                '  - button [ref=old3]\n'
                '  - heading "older response"\n'
                '  - button [ref=new1]\n'
                '  - button [ref=new2]\n'
                '  - button [ref=new3]\n'
                '  - button [ref=new4]\n'
                '  - button [ref=new5]\n'
            )
        )
    )

    actions = semantics.get_last_response_actions()

    assert actions.copy_ref == "new1"
    assert actions.regenerate_ref == "new2"
    assert actions.like_ref == "new3"
    assert actions.dislike_ref == "new4"
    assert actions.share_ref == "new5"


def test_upload_file_returns_false_until_real_file_input_support_exists(tmp_path):
    file_path = tmp_path / "example.txt"
    file_path.write_text("demo")
    a11y = FakeA11y(snapshot="")
    semantics = DeepSeekSemantics(a11y)

    assert semantics.upload_file(str(file_path)) is False
    assert a11y.eval_calls == 0


def test_multi_round_helper_requires_a_fresh_assistant_reply():
    chat = object.__new__(MultiRoundChat)
    baseline = [("user", "earlier question"), ("assistant", "old reply")]

    unchanged = [
        Message(role="user", content="earlier question"),
        Message(role="assistant", content="old reply"),
    ]
    updated = [
        Message(role="user", content="question"),
        Message(role="assistant", content="new reply"),
    ]

    assert chat._extract_turn_response(unchanged, "question", baseline) is None
    assert chat._extract_turn_response(updated, "question", baseline) == "new reply"


def test_run_turn_returns_failure_when_no_fresh_response_arrives():
    bridge = object.__new__(DeepSeekAgentBridge)
    observation = {
        "page_state": {"can_send": True, "has_input": True, "is_streaming": False},
        "messages": [],
        "last_response": {"exists": True, "content": "previous"},
    }

    bridge.observe = lambda: observation
    bridge.act = lambda action: {"success": True}
    bridge._wait_for_response = lambda timeout, baseline_observation=None: False

    result = DeepSeekAgentBridge.run_turn(bridge, "hello", timeout=0.1)

    assert result["success"] is False
    assert result["error"] == "Timeout waiting for response"


def test_wait_for_response_uses_consistent_counter_source():
    """wait_for_response must not mix get_messages() length with fast-state count.

    When the DOM has 2 .ds-message nodes but get_messages() only parses 1
    (e.g., due to virtualization or filtering), using different sources for
    baseline vs. comparison causes the new-reply check to never fire.
    """
    chat = object.__new__(DeepSeekChat)

    class StubSemantics:
        def __init__(self):
            self._msg_count = 1

        def get_fast_state(self):
            return {"message_count": self._msg_count, "is_streaming": False}

        def get_messages(self):
            # Simulate divergence: fast_state sees 2 .ds-message nodes,
            # but parsing only returns 1 message (the old one).
            # The key point is that baseline + comparison both use fast_state
            # count, so the new reply IS detected even though get_messages()
            # has a different length.
            if self._msg_count == 1:
                return [Message(role="user", content="hello")]
            return [
                Message(role="user", content="hello"),
                Message(role="assistant", content="new reply"),
            ]

    stub = StubSemantics()
    chat.semantics = stub

    # After baseline is captured (count=1), bump the DOM count to trigger detection
    def delayed_bump():
        import time
        time.sleep(0.06)
        stub._msg_count = 2

    import threading
    t = threading.Thread(target=delayed_bump)
    t.start()

    result = chat.wait_for_response(timeout=0.5, poll_interval=0.05)
    t.join()
    assert result == "new reply"


def test_has_new_response_accepts_changed_last_response_on_virtualized_dom():
    baseline = {
        "message_count": 5,
        "assistant_count": 3,
        "last_response_content": "DEBUG_OK_2026",
        "thinking_content": 'We need to answer with exactly "DEBUG_OK_2026".',
        "thinking_time": None,
    }
    observation = {
        "page_state": {"message_count": 5},
        "messages": [
            {"role": "assistant", "content": "older"},
            {"role": "user", "content": "Please answer with exactly: LIVE_BRIDGE_OK"},
            {"role": "assistant", "content": "LIVE_BRIDGE_OK"},
        ],
        "last_response": {
            "content": "LIVE_BRIDGE_OK",
            "thinking": {
                "content": 'We need to answer exactly "LIVE_BRIDGE_OK" as per instruction.',
                "time": None,
            },
        },
    }

    assert DeepSeekAgentBridge._has_new_response(baseline, observation) is True
