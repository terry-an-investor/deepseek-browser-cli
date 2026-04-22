"""Layer 3: High-level conversational interface and multi-round manager."""

import json
import re
import time
from typing import Optional

from deepseek_browser_cli.models import (
    ChatMode,
    ChatTurn,
    Conversation,
    Message,
    PageState,
    ResponseAction,
    ThinkingTrace,
)
from deepseek_browser_cli.primitives import A11yPrimitives
from deepseek_browser_cli.semantics import DeepSeekSemantics

# ---------------------------------------------------------------------------
# Layer 3: High-Level Workflow Composition
# ---------------------------------------------------------------------------

class DeepSeekChat:
    """High-level conversational interface.

    Composes Layer 2 semantic actions into complete user workflows.
    Handles retry, state tracking, and error recovery.
    """

    def __init__(self, session: str = "default", auto_connect: bool = False):
        self.layer1 = A11yPrimitives(session=session, auto_connect=auto_connect)
        self.semantics = DeepSeekSemantics(self.layer1)
        self._last_message_count = 0

    # --- Delegated properties ---

    @property
    def session(self) -> str:
        return self.layer1.session

    # --- Navigation ---

    def goto(self, path: str = "/") -> bool:
        url = f"https://chat.deepseek.com{path}"
        _, code = self.layer1.open_url(url)
        return code == 0

    def is_front_page(self) -> bool:
        return self.semantics.get_page_state().is_initial_page

    def get_current_url(self) -> str:
        return self.layer1.get_url()

    # --- Session management ---

    def new_conversation(self) -> bool:
        return self.semantics.new_conversation()

    def list_conversations(self) -> list[Conversation]:
        return self.semantics.list_conversations()

    def select_conversation(self, title: str) -> bool:
        return self.semantics.select_conversation(title)

    # --- Mode ---

    def select_mode(self, mode: ChatMode | str) -> bool:
        if isinstance(mode, str):
            mode = ChatMode(mode)
        return self.semantics.select_mode(mode)

    # --- Input controls ---

    def toggle_deep_thinking(self) -> bool:
        return self.semantics.toggle_deep_thinking()

    def toggle_web_search(self) -> bool:
        return self.semantics.toggle_web_search()

    def upload_file(self, filepath: str) -> bool:
        return self.semantics.upload_file(filepath)

    # --- Messaging ---

    def send_message(self, text: str) -> bool:
        return self.semantics.send_message(text)

    def get_messages(self) -> list[Message]:
        return self.semantics.get_messages()

    def get_thinking_trace(self) -> Optional[ThinkingTrace]:
        return self.semantics.get_thinking_trace()

    # --- Response actions ---

    def copy_last_response(self) -> bool:
        return self.semantics.copy_last_response()

    def regenerate_last_response(self) -> bool:
        return self.semantics.regenerate_last_response()

    def like_last_response(self) -> bool:
        return self.semantics.like_last_response()

    def dislike_last_response(self) -> bool:
        return self.semantics.dislike_last_response()

    def share_last_response(self) -> bool:
        return self.semantics.share_last_response()

    # --- Waiting with state machine ---

    def wait_for_response(self, timeout: float = 60, poll_interval: float = 2.0) -> Optional[str]:
        """Wait for assistant response with state-aware polling."""
        deadline = time.time() + timeout
        # Use the same metric for baseline and comparison to avoid divergence
        # between get_messages() length and fast-state .ds-message count.
        self._last_message_count = self.semantics.get_fast_state()["message_count"]
        last_streaming_check = False

        while time.time() < deadline:
            state = self.semantics.get_fast_state()
            current_count = state["message_count"]

            # New message arrived
            if current_count > self._last_message_count:
                self._last_message_count = current_count
                msgs = self.get_messages()
                for msg in reversed(msgs):
                    if msg.role == "assistant":
                        return msg.content
                return None

            # If streaming, wait longer without counting against timeout as aggressively
            if state["is_streaming"]:
                last_streaming_check = True
                time.sleep(poll_interval)
                continue

            # Was streaming but stopped — give it one more poll for final render
            if last_streaming_check:
                last_streaming_check = False
                time.sleep(poll_interval)
                continue

            time.sleep(poll_interval)

        return None

    def wait_for_page_ready(self, timeout: float = 10) -> bool:
        """Wait until page is ready for interaction (initial page or active chat)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.semantics.get_fast_state()
            if state["has_input"]:
                return True
            time.sleep(0.5)
        return False


# ---------------------------------------------------------------------------
# Layer 3b: Multi-Round Conversation Manager
# ---------------------------------------------------------------------------

class MultiRoundChat:
    """Stateful multi-round conversation with DeepSeek.

    Maintains conversation history, handles streaming responses,
    and provides callback hooks for real-time updates.
    """

    def __init__(
        self,
        session: str = "default",
        auto_connect: bool = False,
        mode: ChatMode = ChatMode.EXPERT,
        enable_thinking: bool = True,
        enable_search: bool = False,
    ):
        self.chat = DeepSeekChat(session=session, auto_connect=auto_connect)
        self.mode = mode
        self.enable_thinking = enable_thinking
        self.enable_search = enable_search
        self.history: list[ChatTurn] = []
        self._is_setup = False

    # --- Setup ---

    def setup(self) -> bool:
        """Initialize the chat session (navigate, set mode, toggles).

        Only navigates to the landing page if we're not already on
        a DeepSeek chat page. This preserves active conversations
        for multi-round continuity.
        """
        if self._is_setup:
            return True

        url = self.chat.get_current_url()
        is_on_deepseek = "chat.deepseek.com" in url

        if not is_on_deepseek:
            self.chat.goto("/")
            self.chat.wait_for_page_ready(timeout=10)

            if self.chat.is_front_page():
                self.chat.select_mode(self.mode)

        # Apply toggle settings
        state = self.chat.semantics.get_page_state()
        if self.enable_thinking and not state.deep_thinking_enabled:
            self.chat.toggle_deep_thinking()
        elif not self.enable_thinking and state.deep_thinking_enabled:
            self.chat.toggle_deep_thinking()

        if self.enable_search and not state.web_search_enabled:
            self.chat.toggle_web_search()
        elif not self.enable_search and state.web_search_enabled:
            self.chat.toggle_web_search()

        self._is_setup = True
        return True

    # --- Core conversation loop ---

    def send(
        self,
        message: str,
        timeout: float = 120,
        on_stream_start: Optional[callable] = None,
        on_token: Optional[callable] = None,
        on_response: Optional[callable] = None,
    ) -> Optional[ChatTurn]:
        """Send a message and wait for the complete response.

        Blocks until the model finishes streaming and the page is ready
        for the next turn.

        Args:
            message: User message text
            timeout: Maximum seconds to wait for response
            on_stream_start: Called when model starts generating
            on_token: Called with each content delta (for streaming UI)
            on_response: Called with the final assistant response

        Returns:
            ChatTurn containing the full exchange, or None on failure
        """
        if not self._is_setup:
            self.setup()

        # Step 1: Wait until previous response is fully done and input is ready.
        if not self._wait_until_ready(timeout=30):
            return None

        baseline = self._message_tail_signature(self.chat.get_messages())

        # Step 2: Send the message.
        if not self.chat.send_message(message):
            return None

        # Step 3: Wait for streaming to start (model picked up the message).
        if on_stream_start:
            stream_detected = self._wait_for_streaming(timeout=10)
            if stream_detected:
                on_stream_start()

        # Step 4: Wait for streaming to finish and capture response.
        response = self._wait_for_complete_response(
            user_message=message,
            timeout=timeout,
            baseline=baseline,
        )

        if response is None:
            return None

        # Extract thinking trace (only available in expert mode with thinking enabled)
        trace = self.chat.get_thinking_trace()

        turn = ChatTurn(
            user_message=message,
            assistant_response=response,
            thinking_trace=trace,
        )
        self.history.append(turn)

        if on_response:
            on_response(response)

        return turn

    def _wait_until_ready(self, timeout: float = 30) -> bool:
        """Wait until the page is ready for a new message.

        Ensures previous response streaming is done and the input field
        is available. Critical for multi-round continuity.
        """
        deadline = time.time() + timeout
        current_interval = 0.3
        while time.time() < deadline:
            state = self.chat.semantics.get_fast_state()
            if not state["is_streaming"] and state["has_input"]:
                return True
            time.sleep(current_interval)
            current_interval = min(current_interval * 1.3, 1.0)
        return False

    @staticmethod
    def _message_tail_signature(messages: list[Message], limit: int = 4) -> list[tuple[str, str]]:
        return [(msg.role, msg.content) for msg in messages[-limit:]]

    def _extract_turn_response(
        self,
        messages: list[Message],
        user_message: str,
        baseline: list[tuple[str, str]],
    ) -> Optional[str]:
        """Return the assistant reply for the current turn only after the tail changes."""
        if baseline and self._message_tail_signature(messages, len(baseline)) == baseline:
            return None

        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.role != "user" or msg.content != user_message:
                continue
            if idx + 1 < len(messages) and messages[idx + 1].role == "assistant":
                return messages[idx + 1].content
            return None
        return None

    def _wait_for_complete_response(
        self,
        user_message: str,
        timeout: float = 120,
        baseline: Optional[list[tuple[str, str]]] = None,
    ) -> Optional[str]:
        """Wait until streaming starts, then finishes, and return response.

        More reliable than message-count comparison because it uses the
        page's streaming state directly.
        """
        deadline = time.time() + timeout

        # Phase 1: Wait for streaming to start (model picked up the message)
        streaming_started = False
        poll_interval = 0.2
        while time.time() < deadline:
            state = self.chat.semantics.get_fast_state()
            if state["is_streaming"]:
                streaming_started = True
                break
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.3, 0.5)

        baseline = baseline or self._message_tail_signature(self.chat.get_messages())

        if not streaming_started:
            # Model may have responded instantly; only accept a fresh reply for this turn.
            return self._extract_turn_response(self.chat.get_messages(), user_message, baseline)

        # Phase 2: Wait for streaming to finish
        poll_interval = 0.3
        while time.time() < deadline:
            state = self.chat.semantics.get_fast_state()
            if not state["is_streaming"]:
                # Give DOM one more moment to settle
                time.sleep(0.3)
                msgs = self.chat.get_messages()
                response = self._extract_turn_response(msgs, user_message, baseline)
                if response:
                    return response
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.2, 1.0)

        return None

    def _wait_for_streaming(self, timeout: float = 5) -> bool:
        """Quick poll to detect if model started streaming."""
        deadline = time.time() + timeout
        poll_interval = 0.2
        while time.time() < deadline:
            state = self.chat.semantics.get_fast_state()
            if state["is_streaming"]:
                return True
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.3, 0.5)
        return False

    # --- Streaming with real-time content ---

    def send_streaming(
        self,
        message: str,
        timeout: float = 120,
        on_token: Optional[callable] = None,
        poll_interval: float = 0.5,
    ) -> Optional[ChatTurn]:
        """Send a message and yield content deltas as they appear.

        Uses DOM diffing to detect new content chunks during streaming.
        Best for building real-time chat UIs.
        """
        if not self._is_setup:
            self.setup()

        # Wait until previous response is fully done and input is ready.
        if not self._wait_until_ready(timeout=30):
            return None

        baseline = self._message_tail_signature(self.chat.get_messages())

        if not self.chat.send_message(message):
            return None

        deadline = time.time() + timeout
        last_content = ""
        response_started = False

        current_interval = max(0.1, poll_interval)
        while time.time() < deadline:
            state = self.chat.semantics.get_fast_state()
            msgs = self.chat.get_messages()
            current = self._extract_turn_response(msgs, message, baseline)

            if state["is_streaming"] or current is not None or response_started:
                response_started = True
                if current and len(current) > len(last_content):
                    delta = current[len(last_content):]
                    if on_token:
                        on_token(delta, current)
                    last_content = current

            if response_started and not state["is_streaming"]:
                # Streaming finished, do one final capture
                final = self._extract_turn_response(self.chat.get_messages(), message, baseline)
                if final:
                    trace = self.chat.get_thinking_trace()
                    turn = ChatTurn(
                        user_message=message,
                        assistant_response=final,
                        thinking_trace=trace,
                    )
                    self.history.append(turn)
                    return turn

            time.sleep(current_interval)
            current_interval = min(current_interval * 1.2, max(poll_interval * 2, 1.0))

        # Timeout — capture whatever we have
        final = self._extract_turn_response(self.chat.get_messages(), message, baseline)
        if final:
            trace = self.chat.get_thinking_trace()
            turn = ChatTurn(
                user_message=message,
                assistant_response=final,
                thinking_trace=trace,
            )
            self.history.append(turn)
            return turn

        return None

    # --- Conversation management ---

    def new_conversation(self) -> bool:
        """Start fresh (clears local history, starts new thread on DeepSeek)."""
        self.history = []
        self._is_setup = False
        return self.chat.new_conversation()

    def get_history(self) -> list[ChatTurn]:
        """Return full conversation history."""
        return list(self.history)

    def get_last_turn(self) -> Optional[ChatTurn]:
        """Return the most recent turn."""
        return self.history[-1] if self.history else None

    def get_context_window(self, n_turns: int = 5) -> list[ChatTurn]:
        """Return the last N turns for context management."""
        return self.history[-n_turns:] if n_turns > 0 else []

    def regenerate_last(self, timeout: float = 120) -> Optional[ChatTurn]:
        """Regenerate the last assistant response."""
        if not self.history:
            return None

        last_turn = self.history[-1]
        self.chat.regenerate_last_response()

        response = self.chat.wait_for_response(timeout=timeout)
        if response is None:
            return None

        trace = self.chat.get_thinking_trace()
        new_turn = ChatTurn(
            user_message=last_turn.user_message,
            assistant_response=response,
            thinking_trace=trace,
        )
        self.history[-1] = new_turn
        return new_turn

    # --- Response actions (convenience) ---

    def copy_last(self) -> bool:
        return self.chat.copy_last_response()

    def like_last(self) -> bool:
        return self.chat.like_last_response()

    def dislike_last(self) -> bool:
        return self.chat.dislike_last_response()

    def share_last(self) -> bool:
        return self.chat.share_last_response()

    # --- Export ---

    @staticmethod
    def _escape_markdown_inline(text: str) -> str:
        escaped = text.replace("\\", "\\\\")
        for ch in ("`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", "!", "|", ">"):
            escaped = escaped.replace(ch, f"\\{ch}")
        return escaped

    @staticmethod
    def _fenced_block(text: str) -> str:
        max_backticks = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
        fence = "`" * max(3, max_backticks + 1)
        return f"{fence}\n{text}\n{fence}"

    def export_markdown(self) -> str:
        """Export conversation as Markdown."""
        lines = []
        for i, turn in enumerate(self.history, 1):
            lines.append(f"## Turn {i}")
            lines.append(f"\n**User:** {self._escape_markdown_inline(turn.user_message)}\n")
            if turn.thinking_trace:
                trace_time = turn.thinking_trace.time or "unknown"
                lines.append(f"\n*Thinking ({self._escape_markdown_inline(trace_time)}):*")
                lines.append(f"{self._fenced_block(turn.thinking_trace.content)}\n")
            lines.append(f"\n**Assistant:** {self._escape_markdown_inline(turn.assistant_response)}\n")
        return "\n".join(lines)

    def export_json(self) -> str:
        """Export conversation as JSON."""
        return json.dumps([
            {
                "user": turn.user_message,
                "assistant": turn.assistant_response,
                "thinking": {
                    "content": turn.thinking_trace.content if turn.thinking_trace else None,
                    "time": turn.thinking_trace.time if turn.thinking_trace else None,
                },
                "timestamp": turn.timestamp,
            }
            for turn in self.history
        ], ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Backwards compatibility aliases
# ---------------------------------------------------------------------------

# Preserve old API surface for existing callers
DeepSeekChat.QUICK = ChatMode.QUICK
DeepSeekChat.EXPERT = ChatMode.EXPERT

# Old method name alias
DeepSeekChat.send_message_via_js = lambda self, text: self.send_message(text)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    chat = DeepSeekChat(session="user-chat", auto_connect=True)
    chat.goto("/")
    chat.select_mode(ChatMode.EXPERT)

    print("Sending: What is 8+8?")
    chat.send_message("What is 8+8?")

    time.sleep(10)

    trace = chat.get_thinking_trace()
    if trace:
        print(f"Thinking ({trace.time}): {trace.content[:50]}...")

    msgs = chat.get_messages()
    print(f"Messages: {len(msgs)}")
    for m in msgs[-3:]:
        preview = m.content[:60] + "..." if len(m.content) > 60 else m.content
        print(f"  [{m.role}]: {preview}")
