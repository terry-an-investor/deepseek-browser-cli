"""Agent Bridge: Real-time observation + generic action execution.

This module gives AI agents full visibility into the DeepSeek page state
via JSON observations, and lets them decide what to do using generic
action primitives.

Pattern:
    1. OBSERVE: agent.observe() -> JSON page state
    2. REASON:  Agent decides what action to take
    3. ACT:     agent.act(action) -> executes
    4. VERIFY:  agent.observe() -> confirms result

No hardcoded button positions, no assumed DOM classes. Everything is
discovered at runtime from the live page.
"""

import ast
import json
import re
import time
from typing import Optional

from deepseek_browser_cli.deepseek_model import (
    A11yPrimitives,
    ChatMode,
    DeepSeekSemantics,
)


# ---------------------------------------------------------------------------
# Observation Layer: Everything the agent can see
# ---------------------------------------------------------------------------

class DeepSeekObserver:
    """Discovers and reports the complete page state as structured JSON."""

    def __init__(self, a11y: A11yPrimitives, semantics: DeepSeekSemantics):
        self.a11y = a11y
        self.semantics = semantics

    # --- High-level: Full page observation ---

    def observe(self) -> dict:
        """Return complete page observation as JSON-serializable dict.

        This is the primary method agents should call to understand
        the current state of the page.
        """
        return {
            "page_state": self._observe_page_state(),
            "input_area": self._observe_input_area(),
            "messages": self._observe_messages(),
            "last_response": self._observe_last_response(),
            "sidebar": self._observe_sidebar(),
            "raw_elements": self._observe_raw_elements(),
        }

    def _observe_page_state(self) -> dict:
        state = self.semantics.get_page_state()
        return {
            "url": state.url,
            "is_initial_page": state.is_initial_page,
            "has_input": state.has_input,
            "is_streaming": state.is_streaming,
            "message_count": state.message_count,
            "mode": state.mode,
            "deep_thinking_enabled": state.deep_thinking_enabled,
            "web_search_enabled": state.web_search_enabled,
            "can_send": state.has_input and not state.is_streaming,
            "notes": []
                + (["page is on landing page, select mode first"] if state.is_initial_page else [])
                + (["model is currently streaming, wait before sending"] if state.is_streaming else [])
                + (["no input field found, page may not be ready"] if not state.has_input else []),
        }

    def _observe_input_area(self) -> dict:
        """Discover the input area elements dynamically."""
        snap = self.a11y.snapshot()
        result = {
            "textbox_ref": None,
            "send_button_ref": None,
            "send_button_disabled": True,
            "deep_thinking_toggle_ref": None,
            "deep_thinking_active": False,
            "web_search_toggle_ref": None,
            "web_search_active": False,
            "upload_button_ref": None,
            "new_chat_button_ref": None,
        }

        for line in snap.split("\n"):
            # Text input
            if 'textbox "给 DeepSeek' in line or 'textarea' in line:
                m = re.search(r"\[ref=([^\]]+)\]", line)
                if m:
                    result["textbox_ref"] = m.group(1)

            # Deep thinking toggle (ref only, state checked via JS below)
            if 'button "深度思考"' in line:
                m = re.search(r"\[ref=([^\]]+)\]", line)
                if m:
                    result["deep_thinking_toggle_ref"] = m.group(1)

            # Web search toggle (ref only, state checked via JS below)
            if 'button "智能搜索"' in line:
                m = re.search(r"\[ref=([^\]]+)\]", line)
                if m:
                    result["web_search_toggle_ref"] = m.group(1)

            # Send button (last button before the disabled one, or the only non-disabled)
            if "button [disabled" in line and "ref=" in line:
                # The send button is the disabled one when empty, or the one before it
                pass

        # Use JS for more reliable discovery of dynamic elements and toggle states
        js = r"""
        (function() {
            const info = {
                textbox: null,
                sendButton: null,
                sendDisabled: true,
                uploadButton: null,
                newChatButton: null,
                deepThinkingActive: false,
                webSearchActive: false
            };

            // Find textarea
            const textarea = document.querySelector('textarea');
            if (textarea) info.textbox = 'textarea';

            // Find send button (typically the last button in input area, or has specific icon)
            const allBtns = document.querySelectorAll('button, [role="button"]');
            allBtns.forEach(btn => {
                const isDisabled = btn.disabled || btn.getAttribute('aria-disabled') === 'true';
                const parent = btn.closest('footer, form, [class*="input"], [class*="chat"]');
                if (parent) {
                    // Check if it's near textarea
                    if (parent.querySelector('textarea')) {
                        // Could be send or upload
                        const hasSendIcon = btn.innerHTML.includes('arrow') ||
                            btn.innerHTML.includes('send') ||
                            btn.getAttribute('aria-label')?.includes('发送');
                        if (hasSendIcon || btn.className.includes('send')) {
                            info.sendButton = btn.className.substring(0, 30);
                            info.sendDisabled = isDisabled;
                        }
                        // Upload button (paperclip icon)
                        const hasUploadIcon = btn.innerHTML.includes('paperclip') ||
                            btn.innerHTML.includes('clip') ||
                            btn.getAttribute('aria-label')?.includes('上传');
                        if (hasUploadIcon) {
                            info.uploadButton = btn.className.substring(0, 30);
                        }
                    }
                }
            });

            // Detect toggle states by CSS class
            const thinkingBtn = Array.from(document.querySelectorAll('button, [role="button"]'))
                .find(b => b.textContent.includes('深度思考'));
            if (thinkingBtn) {
                info.deepThinkingActive = thinkingBtn.classList.contains('ds-toggle-button--selected') ||
                                          thinkingBtn.classList.contains('active') ||
                                          thinkingBtn.getAttribute('aria-pressed') === 'true';
            }

            const searchBtn = Array.from(document.querySelectorAll('button, [role="button"]'))
                .find(b => b.textContent.includes('智能搜索'));
            if (searchBtn) {
                info.webSearchActive = searchBtn.classList.contains('ds-toggle-button--selected') ||
                                       searchBtn.classList.contains('active') ||
                                       searchBtn.getAttribute('aria-pressed') === 'true';
            }

            // New chat button in sidebar
            const sidebar = document.querySelector('nav, aside, [class*="sidebar"]');
            if (sidebar) {
                const firstBtn = sidebar.querySelector('button, [role="button"]');
                if (firstBtn) info.newChatButton = firstBtn.className.substring(0, 30);
            }

            return JSON.stringify(info);
        })()
        """
        result_js, code = self.a11y.eval_js(js)
        if code == 0 and result_js:
            try:
                clean = ast.literal_eval(result_js.strip())
                js_info = json.loads(clean)
                if js_info.get("textbox"):
                    result["textbox_css"] = js_info["textbox"]
                if js_info.get("sendButton"):
                    result["send_button_class"] = js_info["sendButton"]
                result["send_button_disabled"] = js_info.get("sendDisabled", True)
                if js_info.get("uploadButton"):
                    result["upload_button_class"] = js_info["uploadButton"]
                if js_info.get("newChatButton"):
                    result["new_chat_button_class"] = js_info["newChatButton"]
                result["deep_thinking_active"] = js_info.get("deepThinkingActive", False)
                result["web_search_active"] = js_info.get("webSearchActive", False)
            except (ValueError, json.JSONDecodeError):
                pass

        return result

    def _observe_messages(self) -> list[dict]:
        """Return all messages in the conversation."""
        msgs = self.semantics.get_messages()
        return [
            {"role": m.role, "content": m.content}
            for m in msgs
        ]

    def _observe_last_response(self) -> dict:
        """Discover and report the last assistant response with its action buttons.

        Uses direct JS to find the last visible assistant message without
        scrolling, so virtual scrolling doesn't hide the latest response.
        """
        # Use JS to directly extract the last assistant message from current DOM
        js_content = r"""
        (function() {
            const msgElements = document.querySelectorAll('.ds-message');
            if (msgElements.length === 0) return JSON.stringify({exists: false});

            // Find the last assistant message (class starts with 'ds-message')
            let lastAssistant = null;
            for (let i = msgElements.length - 1; i >= 0; i--) {
                const classes = (msgElements[i].className || '').split(/\s+/).filter(Boolean);
                if (classes[0] === 'ds-message') {
                    lastAssistant = msgElements[i];
                    break;
                }
            }
            if (!lastAssistant) return JSON.stringify({exists: false});

            // Extract response content (markdown NOT inside think-content)
            const allMarkdowns = lastAssistant.querySelectorAll('.ds-markdown');
            let content = '';
            for (const md of allMarkdowns) {
                if (!md.closest('.ds-think-content')) {
                    content = md.textContent.trim();
                }
            }

            // Fallback 1: strip thinking indicators from raw text
            if (!content) {
                let raw = lastAssistant.textContent.trim();
                // Strip "正在思考" placeholder (appears while streaming thinking)
                raw = raw.replace(/^(正在思考\s*)+/, '');
                // Strip "已思考（用时 X 秒）" completed thinking header
                raw = raw.replace(/^已思考[（(]用时\s*\d+\s*秒[）)]\s*/, '');
                content = raw;
            }

            // Fallback 2: if content still starts with thinking text, strip it
            content = content.replace(/^(正在思考\s*)+/, '');
            content = content.replace(/^已思考[（(]用时\s*\d+\s*秒[）)]\s*/, '');

            return JSON.stringify({exists: true, content: content.substring(0, 5000)});
        })()
        """
        result_js, code = self.a11y.eval_js(js_content)
        content = None
        exists = False
        if code == 0 and result_js:
            try:
                clean = ast.literal_eval(result_js.strip())
                js_data = json.loads(clean)
                exists = js_data.get("exists", False)
                content = js_data.get("content")
            except (ValueError, json.JSONDecodeError):
                pass

        if not exists or not content:
            # Fallback to get_messages() (no scrolling)
            msgs = self.semantics.get_messages()
            if msgs and msgs[-1].role == "assistant":
                content = msgs[-1].content
                exists = True
            else:
                return {"exists": False, "content": None, "actions": []}

        # Discover action buttons in the ds-flex sibling of the last message
        js = r"""
        (function() {
            const result = {actions: []};
            const positionNames = ["copy", "regenerate", "like", "dislike", "share"];

            const msgElements = document.querySelectorAll('.ds-message');
            if (msgElements.length === 0) return JSON.stringify(result);

            // Get last assistant message
            let lastMsg = null;
            for (let i = msgElements.length - 1; i >= 0; i--) {
                const classes = (msgElements[i].className || '').split(/\s+/).filter(Boolean);
                if (classes[0] === 'ds-message') {
                    lastMsg = msgElements[i];
                    break;
                }
            }
            if (!lastMsg) return JSON.stringify(result);

            // Action buttons live in a ds-flex container near the message
            let actionContainer = null;

            // Try sibling of message's parent
            const parent = lastMsg.parentElement;
            if (parent) {
                const sibling = parent.nextElementSibling;
                if (sibling && sibling.querySelectorAll('.ds-icon-button').length === 5) {
                    actionContainer = sibling;
                }
            }

            // Try parent's sibling
            if (!actionContainer && parent && parent.parentElement) {
                const grandparent = parent.parentElement;
                const children = grandparent.children;
                for (let i = 0; i < children.length; i++) {
                    if (children[i].contains(lastMsg) || children[i] === parent) {
                        // Check next few siblings
                        for (let j = i + 1; j < Math.min(i + 3, children.length); j++) {
                            const btns = children[j].querySelectorAll('.ds-icon-button');
                            if (btns.length === 5) {
                                actionContainer = children[j];
                                break;
                            }
                        }
                        break;
                    }
                }
            }

            // Fallback: scan all ds-flex containers for one with exactly 5 ds-icon-button--m
            if (!actionContainer) {
                const flexContainers = document.querySelectorAll('.ds-flex');
                for (const flex of flexContainers) {
                    const btns = flex.querySelectorAll('.ds-icon-button--m');
                    if (btns.length === 5) {
                        actionContainer = flex;
                        break;
                    }
                }
            }

            if (!actionContainer) return JSON.stringify(result);

            const btns = actionContainer.querySelectorAll('.ds-icon-button');
            btns.forEach((btn, idx) => {
                let action = positionNames[idx] || "unknown";

                // Try to confirm by SVG path content
                const svg = btn.querySelector('svg');
                if (svg) {
                    const pathStr = svg.innerHTML.toLowerCase();
                    if (pathStr.includes('copy') || pathStr.includes('document')) {
                        action = "copy";
                    } else if (pathStr.includes('refresh') || pathStr.includes('redo') || pathStr.includes('rotate')) {
                        action = "regenerate";
                    } else if (pathStr.includes('thumb') && pathStr.includes('up')) {
                        action = "like";
                    } else if (pathStr.includes('thumb') && pathStr.includes('down')) {
                        action = "dislike";
                    } else if (pathStr.includes('share') || pathStr.includes('forward')) {
                        action = "share";
                    }
                }

                result.actions.push({
                    index: idx,
                    action: action,
                    className: btn.className.substring(0, 50),
                    ariaLabel: btn.getAttribute('aria-label') || '',
                    title: btn.title || '',
                    disabled: btn.disabled || btn.getAttribute('aria-disabled') === 'true'
                });
            });

            return JSON.stringify(result);
        })()
        """
        result_js, code = self.a11y.eval_js(js)
        actions = []
        if code == 0 and result_js:
            try:
                clean = ast.literal_eval(result_js.strip())
                js_data = json.loads(clean)
                actions = js_data.get("actions", [])
            except (ValueError, json.JSONDecodeError):
                pass

        # Also get thinking trace
        trace = self.semantics.get_thinking_trace()

        return {
            "exists": True,
            "content": content,
            "thinking": {
                "exists": trace is not None,
                "time": trace.time if trace else None,
                "content": trace.content if trace else None,
            },
            "actions": actions,
        }

    def _observe_sidebar(self) -> dict:
        """Discover sidebar elements: conversation list and new chat button."""
        conversations = self.semantics.list_conversations()
        return {
            "conversations": [
                {"title": c.title, "ref": c.ref, "is_active": c.is_active}
                for c in conversations
            ],
            "new_chat_button": {
                "exists": True,
                "text": "开启新对话",
                "shortcut": "⌘ J",
            },
        }

    def _observe_raw_elements(self) -> list[dict]:
        """Return raw a11y tree elements for advanced debugging.

        This lets agents discover elements we haven't explicitly mapped.
        """
        snap = self.a11y.snapshot()
        elements = []
        for line in snap.split("\n"):
            line = line.strip()
            if not line.startswith("-"):
                continue

            # Parse role
            m = re.match(r'^-\s+(\w+)\s+"([^"]*)"(?:\s+\[ref=([^\]]+)\])?', line)
            if m:
                elements.append({
                    "role": m.group(1),
                    "text": m.group(2),
                    "ref": m.group(3),
                    "raw": line,
                })
            elif "[ref=" in line:
                # Generic clickable without text
                m = re.search(r'(\w+)\s+.*\[ref=([^\]]+)\]', line)
                if m:
                    elements.append({
                        "role": m.group(1),
                        "text": "",
                        "ref": m.group(2),
                        "raw": line,
                    })

        return elements[-50:]  # Last 50 elements (most relevant)


# ---------------------------------------------------------------------------
# Action Layer: Generic primitives the agent can call
# ---------------------------------------------------------------------------

class DeepSeekActor:
    """Generic action primitives. The agent decides WHAT to do;
    this class handles HOW to do it with fallback strategies.
    """

    def __init__(self, a11y: A11yPrimitives, semantics: DeepSeekSemantics):
        self.a11y = a11y
        self.semantics = semantics

    # --- Generic primitives ---

    def click(self, ref: Optional[str] = None, selector: Optional[str] = None) -> bool:
        """Click an element by ref or CSS selector."""
        if ref:
            return self.a11y.click_by_ref(ref)
        if selector:
            js = f"""
            (function() {{
                const el = document.querySelector({json.dumps(selector)});
                if (el) {{ el.click(); return 'clicked'; }}
                return 'not found';
            }})()
            """
            result, code = self.a11y.eval_js(js)
            return code == 0 and "clicked" in result
        return False

    def type(self, text: str, ref: Optional[str] = None, selector: Optional[str] = None) -> bool:
        """Type text into an input by ref or CSS selector."""
        if ref:
            return self.a11y.type_by_ref(ref, text)
        if selector:
            js = f"""
            (function() {{
                const el = document.querySelector({json.dumps(selector)});
                if (!el) return 'not found';
                const pd = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                if (pd && pd.set) pd.set.call(el, {json.dumps(text)});
                else el.value = {json.dumps(text)};
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                return 'typed';
            }})()
            """
            result, code = self.a11y.eval_js(js)
            return code == 0 and "typed" in result
        return False

    def press(self, key: str) -> bool:
        """Press a keyboard key."""
        return self.a11y.press_key(key)

    def eval_js(self, script: str) -> tuple[str, int]:
        """Execute arbitrary JavaScript and return (result, exit_code)."""
        return self.a11y.eval_js(script)

    # --- Composed actions (agent can call these or build its own) ---

    def send_message(self, text: str) -> bool:
        """Send a message using best available strategy."""
        return self.semantics.send_message(text)

    def select_mode(self, mode: str) -> bool:
        """Select chat mode: 'quick' or 'expert'."""
        chat_mode = ChatMode.EXPERT if mode == "expert" else ChatMode.QUICK
        return self.semantics.select_mode(chat_mode)

    def toggle_feature(self, feature: str) -> bool:
        """Toggle a feature by name: 'deep_thinking' or 'web_search'."""
        if feature == "deep_thinking":
            return self.semantics.toggle_deep_thinking()
        elif feature == "web_search":
            return self.semantics.toggle_web_search()
        return False

    def click_response_action(self, action: str) -> bool:
        """Click a response action by name: copy, regenerate, like, dislike, share."""
        actions = {
            "copy": self.semantics.copy_last_response,
            "regenerate": self.semantics.regenerate_last_response,
            "like": self.semantics.like_last_response,
            "dislike": self.semantics.dislike_last_response,
            "share": self.semantics.share_last_response,
        }
        fn = actions.get(action)
        return fn() if fn else False


# ---------------------------------------------------------------------------
# Agent Bridge: Combines observer + actor for autonomous operation
# ---------------------------------------------------------------------------

class DeepSeekAgentBridge:
    """Complete bridge between an AI agent and the DeepSeek web UI.

    The agent calls observe() to get state, decides what to do,
    then calls act() to execute. No hardcoded workflows.
    """

    def __init__(
        self,
        session: str = "default",
        auto_connect: bool = False,
        cdp: Optional[str] = None,
        profile: Optional[str] = None,
        headed: bool = False,
    ):
        self.a11y = A11yPrimitives(
            session=session,
            auto_connect=auto_connect,
            cdp=cdp,
            profile=profile,
            headed=headed,
        )
        self.semantics = DeepSeekSemantics(self.a11y)
        self.observer = DeepSeekObserver(self.a11y, self.semantics)
        self.actor = DeepSeekActor(self.a11y, self.semantics)
        self._action_history: list[dict] = []

    # --- Observation ---

    def observe(self) -> dict:
        """Get complete page observation."""
        return self.observer.observe()

    def observe_compact(self) -> str:
        """Get observation as compact JSON string."""
        return json.dumps(self.observe(), ensure_ascii=False, indent=2)

    # --- Action ---

    def act(self, action: dict) -> dict:
        """Execute an action and return the result.

        Args:
            action: dict with keys:
                - "type": "click" | "type" | "press" | "send" | "toggle" | "mode" | "new_chat" | "click_action" | "eval"
                - "params": dict of parameters for the action type

        Returns:
            dict with "success" (bool) and "result" or "error"
        """
        action_type = action.get("type")
        params = action.get("params", {})
        result = {"success": False, "action": action_type}

        try:
            if action_type == "click":
                result["success"] = self.actor.click(
                    ref=params.get("ref"),
                    selector=params.get("selector"),
                )

            elif action_type == "type":
                result["success"] = self.actor.type(
                    text=params["text"],
                    ref=params.get("ref"),
                    selector=params.get("selector"),
                )

            elif action_type == "press":
                result["success"] = self.actor.press(params["key"])

            elif action_type == "send":
                result["success"] = self.actor.send_message(params["text"])

            elif action_type == "toggle":
                result["success"] = self.actor.toggle_feature(params["feature"])

            elif action_type == "mode":
                result["success"] = self.actor.select_mode(params["mode"])

            elif action_type == "new_chat":
                result["success"] = self.semantics.new_conversation()

            elif action_type == "click_action":
                result["success"] = self.actor.click_response_action(params["action"])

            elif action_type == "eval":
                out, code = self.actor.eval_js(params["script"])
                result["success"] = code == 0
                result["output"] = out

            else:
                result["error"] = f"Unknown action type: {action_type}"

        except Exception as e:
            result["error"] = str(e)

        self._action_history.append({
            "action": action,
            "result": result,
            "timestamp": time.time(),
        })

        return result

    def get_action_history(self) -> list[dict]:
        """Return history of all actions taken."""
        return list(self._action_history)

    # --- High-level: Full autonomous turn ---

    def run_turn(self, message: str, timeout: float = 120) -> dict:
        """Run one complete turn: observe, send, wait, observe.

        Returns the final observation including the response.
        """
        deadline = time.time() + timeout

        # Initial observation
        obs_before = self.observe()

        # Wait until ready
        if not self._is_ready(obs_before["page_state"]):
            remaining = max(0.0, deadline - time.time())
            if remaining <= 0 or not self._wait_until_ready(timeout=remaining):
                return {
                    "success": False,
                    "error": "Timeout waiting for page to be ready",
                    "observation": self.observe(),
                    "user_message": message,
                    "assistant_response": None,
                }
            obs_before = self.observe()

        # Send message
        send_result = self.act({"type": "send", "params": {"text": message}})
        if not send_result["success"]:
            return {
                "success": False,
                "error": "Failed to send message",
                "observation": self.observe(),
            }

        # Wait for response
        remaining = max(0.0, deadline - time.time())
        if remaining <= 0 or not self._wait_for_response(timeout=remaining, baseline_observation=obs_before):
            obs_after = self.observe()
            return {
                "success": False,
                "error": "Timeout waiting for response",
                "observation": obs_after,
                "user_message": message,
                "assistant_response": obs_after.get("last_response", {}).get("content"),
            }

        # Final observation with response
        obs_after = self.observe()
        last_response = obs_after.get("last_response", {})
        if not last_response.get("exists"):
            return {
                "success": False,
                "error": "No response detected",
                "observation": obs_after,
                "user_message": message,
                "assistant_response": None,
            }

        return {
            "success": True,
            "observation": obs_after,
            "user_message": message,
            "assistant_response": last_response.get("content"),
        }

    @staticmethod
    def _is_ready(state: dict) -> bool:
        """Return whether the page can accept a new message."""
        return state.get("can_send", state.get("has_input") and not state.get("is_streaming"))

    @staticmethod
    def _response_marker(observation: dict) -> dict:
        """Capture the conversation state needed to detect a new assistant reply."""
        messages = observation.get("messages", [])
        last_response = observation.get("last_response", {})
        thinking = last_response.get("thinking", {})
        return {
            "message_count": observation.get("page_state", {}).get("message_count", 0),
            "assistant_count": sum(1 for msg in messages if msg.get("role") == "assistant"),
            "last_response_content": last_response.get("content"),
            "thinking_content": thinking.get("content"),
            "thinking_time": thinking.get("time"),
        }

    @classmethod
    def _has_new_response(cls, baseline: dict, observation: dict) -> bool:
        """Return whether observation contains a newer assistant reply than baseline."""
        current = cls._response_marker(observation)
        if current["assistant_count"] > baseline["assistant_count"]:
            return True
        if (
            current["last_response_content"]
            and current["last_response_content"] != baseline["last_response_content"]
        ):
            return True
        if (
            current["message_count"] > baseline["message_count"]
            and current["last_response_content"]
            and current["last_response_content"] != baseline["last_response_content"]
        ):
            return True
        if (
            current["thinking_content"]
            and current["thinking_content"] != baseline["thinking_content"]
            and current["thinking_time"] != baseline["thinking_time"]
        ):
            return True
        return False

    def _wait_until_ready(self, timeout: float = 30) -> bool:
        deadline = time.time() + timeout
        poll_interval = 0.2
        while time.time() < deadline:
            state = self.semantics.get_fast_state()
            if state["has_input"] and not state["is_streaming"]:
                return True
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.3, 1.0)
        return False

    def _wait_for_response(self, timeout: float = 120, baseline_observation: Optional[dict] = None) -> bool:
        """Wait for a fresh response to arrive and settle."""
        deadline = time.time() + timeout
        streaming_started = False
        baseline = self._response_marker(baseline_observation) if baseline_observation else None
        poll_interval = 0.2
        start_time = time.time()
        # Probe quickly for instant non-streaming replies, but avoid full
        # observe() on every loop before streaming begins.
        non_stream_probe_at = start_time + 0.25

        while time.time() < deadline:
            now = time.time()
            state = self.semantics.get_fast_state()
            if state["is_streaming"]:
                streaming_started = True

            # Check for response whenever page is ready (not streaming).
            # For non-streaming instant replies, run periodic probes soon after
            # send; once streaming has started, probe immediately after stop.
            if not state["is_streaming"] and (streaming_started or now >= non_stream_probe_at):
                obs = self.observe()
                ready = self._is_ready(obs["page_state"])
                has_new = baseline is None or self._has_new_response(baseline, obs)
                if ready and has_new:
                    time.sleep(0.2)  # Let DOM settle
                    return True
                if not streaming_started:
                    non_stream_probe_at = now + 0.25

            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.3, 0.5 if streaming_started else 0.3)

        return False


# ---------------------------------------------------------------------------
# System prompt for LLM agents
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """You are an AI agent controlling the DeepSeek web chat interface via browser automation.

## Your Capabilities

You can OBSERVE the page state and ACT on it. You do not have hardcoded knowledge of the UI — you discover everything through observation.

## Observation Format

The observe() method returns JSON with:

```json
{
  "page_state": {
    "url": "...",
    "is_streaming": true/false,      // CRITICAL: if true, you must wait
    "has_input": true/false,          // Can you send a message?
    "can_send": true/false,           // Combined: has_input AND not streaming
    "mode": "expert" or "quick",
    "deep_thinking_enabled": true/false,
    "web_search_enabled": true/false,
    "notes": ["human-readable hints"]
  },
  "input_area": {
    "textbox_ref": "e123",            // Use this ref for typing
    "send_button_disabled": true/false,
    "deep_thinking_toggle_ref": "...",
    "web_search_toggle_ref": "...",
    "upload_button_ref": "..."
  },
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "last_response": {
    "exists": true/false,
    "content": "...",
    "thinking": {"exists": true, "time": "2 秒", "content": "..."},
    "actions": [
      {"index": 0, "action": "copy", "className": "...", "disabled": false},
      {"index": 1, "action": "regenerate", "className": "...", "disabled": false},
      {"index": 2, "action": "like", "className": "...", "disabled": false},
      {"index": 3, "action": "dislike", "className": "...", "disabled": false},
      {"index": 4, "action": "share", "className": "...", "disabled": false}
    ]
  },
  "sidebar": {
    "conversations": [{"title": "...", "ref": "..."}]
  }
}
```

## Action Format

To act, call act() with:

```json
{"type": "TYPE", "params": {...}}
```

Types:
- `"send"` → `{"text": "your message"}` — Send a chat message (waits for streaming)
- `"click"` → `{"ref": "e123"}` or `{"selector": "button.copy"}` — Click an element
- `"type"` → `{"text": "...", "ref": "e123"}` — Type into an input
- `"press"` → `{"key": "Enter"}` — Press a key
- `"toggle"` → `{"feature": "deep_thinking"}` or `{"feature": "web_search"}`
- `"mode"` → `{"mode": "expert"}` or `{"mode": "quick"}`
- `"click_action"` → `{"action": "copy"}` — Click response action (copy/regenerate/like/dislike/share)
- `"eval"` → `{"script": "document.title"}` — Run arbitrary JS

## Rules

1. ALWAYS observe first before acting
2. If `page_state.is_streaming` is true, WAIT — do not send messages or click buttons
3. If `page_state.is_initial_page` is true, select a mode first (`{"type": "mode", "params": {"mode": "expert"}}`)
4. Use `last_response.actions` to discover what you can do with the latest response
5. If something fails, observe again — the page may have changed
6. When sending a message, the page will stream. You must wait for `is_streaming` to become false before sending again
"""
