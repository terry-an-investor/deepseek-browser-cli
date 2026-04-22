import ast
import json
import os
import re
import time
from typing import Optional

from deepseek_browser_cli.models import ChatMode, Conversation, Message, PageState, ResponseAction, ThinkingTrace
from deepseek_browser_cli.primitives import A11yPrimitives

# Re-export _run for send_message strategy
from deepseek_browser_cli.primitives import _run

class DeepSeekSemantics:
    """Page-specific semantic actions built on Layer 1.

    Each action implements multiple fallback strategies so that when
    one selector breaks (CSS class changes, DOM structure changes),
    the next strategy is attempted automatically.
    """

    UI_CHROME = frozenset([
        "深度思考", "智能搜索", "开启新对话", "给 DeepSeek",
        "内容由 AI 生成", "昨天", "今天", "7 天内", "30 天内",
        "⌘ J", "快速模式", "专家模式",
    ])
    ACTIVE_A11Y_TOKENS = (
        "pressed",
        "selected",
        "checked",
        "active",
        "aria-pressed=true",
        "aria-checked=true",
    )

    THINKING_INDICATOR = "已思考"
    THINKING_TIME_PATTERN = re.compile(r"用时\s*([^）]+)")

    def __init__(self, layer1: A11yPrimitives):
        self.a11y = layer1

    def _eval_json(self, script: str) -> Optional[dict]:
        """Evaluate JS and parse JSON result with backward compatibility.

        Prefers ``a11y.eval_json`` if available; otherwise falls back to
        ``eval_js`` + manual unwrapping so existing test doubles and custom
        adapters continue to work.
        """
        if hasattr(self.a11y, "eval_json"):
            return self.a11y.eval_json(script)
        result, code = self.a11y.eval_js(script)
        if code != 0 or not result:
            return None
        text = result.strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        try:
            clean = ast.literal_eval(text)
            if isinstance(clean, str):
                data = json.loads(clean)
                if isinstance(data, dict):
                    return data
        except (ValueError, SyntaxError, json.JSONDecodeError):
            pass
        return None

    # --- Fast state checks (single JS eval for polling loops) ---

    def get_fast_state(self) -> dict:
        """Return lightweight page state via one JS eval.

        Suitable for tight polling loops — only checks streaming status,
        input availability, and message count.  Much cheaper than
        get_page_state() which does snapshot + URL + multi-strategy detection.
        """
        js = r"""
        (function() {
            const textarea = document.querySelector('textarea');
            const hasInput = !!textarea;
            const spinner = document.querySelector(
                '.ds-loading, [class*="loading"], [class*="spinner"], [aria-busy="true"]'
            );
            const isStreaming = !!spinner;
            const msgCount = document.querySelectorAll('.ds-message').length;
            return JSON.stringify({has_input: hasInput, is_streaming: isStreaming, message_count: msgCount});
        })()
        """
        data = self._eval_json(js)
        if data:
            return {
                "has_input": data.get("has_input", False),
                "is_streaming": data.get("is_streaming", False),
                "message_count": data.get("message_count", 0),
            }
        return {"has_input": False, "is_streaming": False, "message_count": 0}

    # --- Page state ---

    def get_page_state(self) -> PageState:
        snap = self.a11y.snapshot()
        url = self.a11y.get_url()
        msgs = self._parse_messages_scoped(snap)

        # Language-agnostic detection via DOM structure
        js = r"""
        (function() {
            const info = {
                is_initial_page: false,
                has_input: false,
                is_streaming: false,
                deep_thinking: false,
                web_search: false,
                mode: 'unknown'
            };

            const radios = Array.from(document.querySelectorAll('[role="radio"], input[type="radio"]'));
            const textarea = document.querySelector('textarea');
            const messages = document.querySelectorAll('.ds-message');
            // Initial page: has mode radios, an input, and no rendered chat messages yet.
            if (radios.length >= 2 && textarea && messages.length === 0) {
                info.is_initial_page = true;
            }

            // Has input field
            info.has_input = !!textarea;

            // Streaming: rely on explicit busy/loading markers in active DOM.
            const spinner = document.querySelector(
                '.ds-loading, [class*="loading"], [class*="spinner"], [aria-busy="true"]'
            );
            info.is_streaming = !!spinner;

            // Toggle states via CSS class (language-agnostic)
            const allBtns = document.querySelectorAll('button, [role="button"]');
            allBtns.forEach(btn => {
                const text = btn.textContent || '';
                const isActive = btn.classList.contains('ds-toggle-button--selected') ||
                                 btn.classList.contains('active') ||
                                 btn.getAttribute('aria-pressed') === 'true';
                // DeepThink toggle: icon or text contains "think" or "思考"
                if ((text.includes('思考') || text.includes('Think') || text.includes('think')) &&
                    text.length < 20) {
                    info.deep_thinking = isActive;
                }
                // Search toggle: icon or text contains "search" or "搜索"
                if ((text.includes('搜索') || text.includes('Search') || text.includes('search')) &&
                    text.length < 20) {
                    info.web_search = isActive;
                }
            });

            // Mode detection (language-agnostic)
            // Strategy 1: On initial page, check which radio is selected
            const modeRadios = document.querySelectorAll('[role="radio"], input[type="radio"]');
            for (const radio of modeRadios) {
                const isChecked = radio.getAttribute('aria-checked') === 'true' || radio.checked;
                const label = (radio.textContent || radio.getAttribute('aria-label') || '').trim();
                if (isChecked) {
                    if (/instant|快速|quick/i.test(label)) {
                        info.mode = 'instant';
                        break;
                    }
                    if (/expert|专家/i.test(label)) {
                        info.mode = 'expert';
                        break;
                    }
                }
            }
            // Strategy 2: On conversation page, look for heading "Start chatting with X"
            if (info.mode === 'unknown') {
                const headings = document.querySelectorAll('h1, h2, h3, h4, h5, h6, div, span');
                for (const h of headings) {
                    const txt = h.textContent.trim();
                    const match = txt.match(/(?:Start chatting with|开始使用)\s+(Instant|Expert|快速模式|专家模式)/i);
                    if (match) {
                        const m = match[1].toLowerCase();
                        info.mode = (m === 'instant' || m === '快速模式') ? 'instant' : 'expert';
                        break;
                    }
                }
            }
            // Strategy 3: Look for mode label on conversation page response headers
            if (info.mode === 'unknown') {
                const modeLabels = document.querySelectorAll('[class*="mode"], [class*="model"]');
                for (const label of modeLabels) {
                    const txt = label.textContent.trim();
                    if (/^Instant$|^快速模式$/i.test(txt)) {
                        info.mode = 'instant';
                        break;
                    }
                    if (/^Expert$|^专家模式$/i.test(txt)) {
                        info.mode = 'expert';
                        break;
                    }
                }
            }

            return JSON.stringify(info);
        })()
        """
        dom_info = self._eval_json(js) or {
            "is_initial_page": False,
            "has_input": False,
            "is_streaming": False,
            "deep_thinking": False,
            "web_search": False,
            "mode": "unknown",
        }

        has_input = dom_info.get("has_input", False) or ("textarea" in snap or "textbox" in snap)
        # Fallback to a11y text heuristics if JS fails
        is_initial = dom_info.get("is_initial_page", False)
        if not is_initial:
            has_mode_selector = any(
                label in snap
                for label in ('radio "快速模式"', 'radio "Instant"', 'radio "专家模式"', 'radio "Expert"')
            )
            is_initial = has_input and len(msgs) == 0 and has_mode_selector
        is_streaming = dom_info.get("is_streaming", False)
        deep_thinking = dom_info.get("deep_thinking", False)
        if not deep_thinking:
            deep_thinking = self._infer_toggle_state_from_snapshot(snap, ("深度思考", "DeepThink"))

        web_search = dom_info.get("web_search", False)
        if not web_search:
            web_search = self._infer_toggle_state_from_snapshot(snap, ("智能搜索", "Search"))

        # Detect mode: trust JS detection first, then query selected radio
        mode = dom_info.get("mode", "unknown")
        if (mode == "unknown" or not mode) and is_initial:
            # Use JS to check which radio is actually selected
            js_mode = r"""
            (function() {
                const radios = document.querySelectorAll('[role="radio"]');
                for (const r of radios) {
                    if (r.getAttribute('aria-checked') === 'true') {
                        const text = r.textContent || '';
                        if (/instant|快速/i.test(text)) return 'instant';
                        if (/expert|专家/i.test(text)) return 'expert';
                    }
                }
                return 'unknown';
            })()
            """
            result, code = self.a11y.eval_js(js_mode)
            if code == 0 and result:
                try:
                    mode = ast.literal_eval(result.strip()).strip('"')
                except (ValueError, SyntaxError):
                    pass

        return PageState(
            url=url,
            is_initial_page=is_initial,
            has_input=has_input,
            is_streaming=is_streaming,
            message_count=len(msgs),
            mode=mode,
            deep_thinking_enabled=deep_thinking,
            web_search_enabled=web_search,
        )

    def _infer_toggle_state_from_snapshot(self, snapshot: str, labels: tuple[str, ...]) -> bool:
        """Infer whether a toggle is active from a11y snapshot metadata."""
        label_patterns = tuple(f'button "{label}"' for label in labels)
        found_match = False
        found_active = False
        for line in snapshot.split("\n"):
            if any(pattern in line for pattern in label_patterns):
                found_match = True
                lowered = line.lower()
                if any(token in lowered for token in self.ACTIVE_A11Y_TOKENS):
                    found_active = True
        return found_match and found_active

    # --- Session / Sidebar management ---

    def new_conversation(self) -> bool:
        """Start a new conversation by clicking the sidebar new-chat button."""
        snap = self.a11y.snapshot()
        lines = snap.split("\n")

        # Strategy 1: click the actual a11y ref for the visible New chat affordance.
        for idx, line in enumerate(lines):
            if 'StaticText "开启新对话"' not in line and 'StaticText "New chat"' not in line:
                continue
            for prev in range(idx - 1, max(-1, idx - 4), -1):
                ref_match = re.search(r"\[ref=([^\]]+)\]", lines[prev])
                if ref_match and ("clickable" in lines[prev] or "button" in lines[prev]):
                    return self.a11y.click_by_ref(ref_match.group(1))

        # Strategy 2: Direct JS
        return self._try_click_new_chat_via_js()

    def _try_click_new_chat_via_js(self) -> bool:
        js = """
        (function() {
            // Find by known labels first (Chinese + English).
            const btn = document.querySelector('button[aria-label="\\u5f00\\u542f\\u65b0\\u5bf9\\u8bdd"]')
                || document.querySelector('button[aria-label="New chat"]')
                || document.querySelector('[title="\\u5f00\\u542f\\u65b0\\u5bf9\\u8bdd"]')
                || document.querySelector('[title="New chat"]')
                || document.querySelector('div[aria-label="\\u5f00\\u542f\\u65b0\\u5bf9\\u8bdd"]')
                || document.querySelector('div[aria-label="New chat"]')
                || document.querySelector('[class*="new"] button');
            if (btn) { btn.click(); return 'clicked'; }

            // Fallback: find a clickable element whose visible text is New chat.
            const candidates = Array.from(document.querySelectorAll('button, [role="button"], a, div, span'));
            const textMatch = candidates.find(el => {
                const text = (el.textContent || '').trim();
                if (!text) return false;
                if (!(text.includes('New chat') || text.includes('\\u5f00\\u542f\\u65b0\\u5bf9\\u8bdd'))) return false;
                return typeof el.click === 'function';
            });
            if (textMatch) {
                const clickable = textMatch.closest('button, [role="button"], a, [onclick], [tabindex]') || textMatch;
                clickable.click();
                return 'clicked-by-text';
            }

            // Fallback: find the first icon-button in sidebar
            const sidebar = document.querySelector('nav, aside, [class*="sidebar"]');
            if (sidebar) {
                const firstBtn = sidebar.querySelector('.ds-icon-button');
                if (firstBtn) { firstBtn.click(); return 'clicked-sidebar-first'; }
            }
            return 'not found';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "not found" not in result

    def list_conversations(self) -> list[Conversation]:
        """Extract conversation list from sidebar."""
        snap = self.a11y.snapshot()
        conversations = []
        in_sidebar = False
        current_url = self.a11y.get_url()

        for line in snap.split("\n"):
            if 'StaticText "今天"' in line or 'StaticText "昨天"' in line:
                in_sidebar = True
                continue
            if not in_sidebar:
                continue
            # Look for link elements with conversation titles
            m = re.match(r'^\s+-\s+link\s+"([^"]+)"\s+\[ref=([^\]]+)\]', line)
            if m:
                title = m.group(1)
                ref = m.group(2)
                # Skip if it looks like UI chrome
                if title in self.UI_CHROME or any(ui in title for ui in self.UI_CHROME):
                    continue
                conversations.append(Conversation(title=title, ref=ref))

        return conversations

    def select_conversation(self, title: str) -> bool:
        """Select a conversation from sidebar by title."""
        conversations = self.list_conversations()
        for conv in conversations:
            if conv.title == title and conv.ref:
                return self.a11y.click_by_ref(conv.ref)
        # Fallback: JS click by text
        js = f"""
        (function() {{
            const links = document.querySelectorAll('a, [role="link"]');
            for (const link of links) {{
                if (link.textContent.trim() === {json.dumps(title)}) {{
                    link.click();
                    return 'clicked';
                }}
            }}
            return 'not found';
        }})()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "not found" not in result

    # --- Mode selection (with fallback) ---

    def select_mode(self, mode: ChatMode) -> bool:
        """Select chat mode with multiple fallback strategies."""
        state = self.get_page_state()
        if not state.is_initial_page:
            return False

        # Try text-based matching first (uses @ref click, more reliable)
        labels = [mode.value]
        if mode == ChatMode.QUICK:
            labels.extend(["Instant", "快速模式"])
        elif mode == ChatMode.EXPERT:
            labels.extend(["Expert", "专家模式"])

        for label in labels:
            if self._try_click_radio(label):
                time.sleep(0.5)
                return True

        # Fallback: index-based JS click
        target_index = 0 if mode == ChatMode.QUICK else 1
        js = f"""
        (function() {{
            const radios = document.querySelectorAll('[role="radio"], input[type="radio"]');
            if (radios.length >= {target_index + 1}) {{
                const radio = radios[{target_index}];
                radio.click();
                radio.focus();
                radio.setAttribute('aria-checked', 'true');
                if (radio.tagName === 'INPUT') radio.checked = true;
                radio.dispatchEvent(new Event('change', {{ bubbles: true }}));
                return 'clicked';
            }}
            return 'not found';
        }})()
        """
        result, code = self.a11y.eval_js(js)
        if code == 0 and "clicked" in result:
            time.sleep(0.5)
            return True

        # Final fallback: parent text matching
        for label in labels:
            if self._try_click_by_parent_text(label):
                return True
            if self._try_click_mode_via_js(label):
                return True

        return False

    def _try_click_radio(self, label: str) -> bool:
        """Strategy 1: Find radio button whose accessible label matches."""
        snap = self.a11y.snapshot()
        for line in snap.split("\n"):
            if 'radio "' in line and label in line:
                ref_match = re.search(r"\[ref=([^\]]+)\]", line)
                if ref_match:
                    return self.a11y.click_by_ref(ref_match.group(1))
        return False

    def _try_click_by_parent_text(self, label: str) -> bool:
        """Strategy 2: Original parent-text approach."""
        js = """
(function() {
    const modeDivs = document.querySelectorAll('[class*="_"]');
    for (const div of modeDivs) {
        const parent = div.parentElement;
        if (parent && parent.textContent.includes(__LABEL__)) {
            div.click();
            return 'clicked';
        }
    }
    return 'not found';
})()
""".replace("__LABEL__", json.dumps(label))
        result, code = self.a11y.eval_js(js)
        return code == 0 and "not found" not in result

    def _try_click_mode_via_js(self, label: str) -> bool:
        """Strategy 3: Broader JS search by text content."""
        js = """
(function() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let node;
    while (node = walker.nextNode()) {
        if (node.textContent.trim() === __LABEL__) {
            const clickable = node.closest('label, button, div[role="radio"]') || node;
            clickable.click();
            return 'clicked';
        }
    }
    return 'not found';
})()
""".replace("__LABEL__", json.dumps(label))
        result, code = self.a11y.eval_js(js)
        return code == 0 and "not found" not in result

    # --- Input controls ---

    def toggle_deep_thinking(self) -> bool:
        """Toggle the Deep Thinking (深度思考) feature."""
        snap = self.a11y.snapshot()
        for line in snap.split("\n"):
            if 'button "深度思考"' in line:
                ref_match = re.search(r"\[ref=([^\]]+)\]", line)
                if ref_match:
                    return self.a11y.click_by_ref(ref_match.group(1))
        # JS fallback
        js = """
        (function() {
            const btn = Array.from(document.querySelectorAll('button'))
                .find(b => b.textContent.includes('\\u6df1\\u5ea6\\u601d\\u8003'));
            if (btn) { btn.click(); return 'toggled'; }
            return 'not found';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "not found" not in result

    def toggle_web_search(self) -> bool:
        """Toggle the Web Search (智能搜索) feature."""
        snap = self.a11y.snapshot()
        for line in snap.split("\n"):
            if 'button "智能搜索"' in line:
                ref_match = re.search(r"\[ref=([^\]]+)\]", line)
                if ref_match:
                    return self.a11y.click_by_ref(ref_match.group(1))
        # JS fallback
        js = """
        (function() {
            const btn = Array.from(document.querySelectorAll('button'))
                .find(b => b.textContent.includes('\\u667a\\u80fd\\u641c\\u7d22'));
            if (btn) { btn.click(); return 'toggled'; }
            return 'not found';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "not found" not in result

    def upload_file(self, filepath: str) -> bool:
        """Return False until file upload is implemented with real CDP support."""
        if not os.path.exists(filepath):
            return False
        return False

    # --- Message sending (with fallback) ---

    def send_message(self, text: str) -> bool:
        """Send a message using the best available strategy.

        Uses CSS selectors instead of @ref to avoid SIGTRAP crashes on macOS
        when Playwright interacts with Chromium via CDP.
        """
        if not text or not text.strip():
            return False

        # Strategy 1: CSS selector via agent-browser fill (avoids SIGTRAP)
        if self._try_send_via_selector(text):
            return True

        # Strategy 2: JavaScript fallback
        if self._try_send_via_js(text):
            return True

        return False

    def _try_send_via_selector(self, text: str) -> bool:
        """Strategy 1: Find textarea via CSS selector and fill."""
        # Use agent-browser snapshot to detect input type
        snap = self.a11y.snapshot()
        selector = "textarea" if "textarea" in snap else "input[type=text]"

        # Use agent-browser fill + press commands (CSS selectors, not @ref)
        cmd = self.a11y._cmd_base + ["fill", selector, text]
        _, code = _run(cmd)
        if code != 0:
            return False

        cmd = self.a11y._cmd_base + ["press", "Enter"]
        _, code = _run(cmd)
        return code == 0

    def _try_send_via_js(self, text: str) -> bool:
        safe = json.dumps(text)
        js = """
(function() {
    const textarea = document.querySelector('textarea');
    if (!textarea) return 'no textarea';
    const pd = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
    if (pd && pd.set) pd.set.call(textarea, %s);
    else textarea.value = %s;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    textarea.focus();
    const enter = new KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true
    });
    textarea.dispatchEvent(enter);
    return 'sent';
})()
""" % (safe, safe)
        result, code = self.a11y.eval_js(js)
        return code == 0 and "no textarea" not in result

    # --- Response actions ---

    def get_last_response_actions(self) -> ResponseAction:
        """Extract action buttons from the last assistant response.

        The buttons appear in fixed order after the response text:
        copy, regenerate, like, dislike, share
        """
        snap = self.a11y.snapshot()
        lines = snap.split("\n")

        # Group button refs by proximity instead of indentation to avoid
        # hierarchy-dependent parsing breakage.
        button_groups: list[list[str]] = []
        current_group: list[str] = []
        last_btn_idx = -99
        for idx, line in enumerate(lines):
            m = re.search(r'-\s+button(?:\s+"[^"]*")?.*\[ref=([^\]]+)\]', line)
            if not m:
                if current_group and re.search(r"-\s+image\b", line):
                    continue
                if current_group:
                    if len(current_group) >= 3:
                        button_groups.append(current_group)
                    current_group = []
                continue

            ref = m.group(1)
            if current_group and (idx - last_btn_idx) > 2:
                if len(current_group) >= 3:
                    button_groups.append(current_group)
                current_group = []
            current_group.append(ref)
            last_btn_idx = idx

        if len(current_group) >= 3:
            button_groups.append(current_group)

        if not button_groups:
            return ResponseAction(None, None, None, None, None)

        # Use the last group (most recent response)
        last_group = button_groups[-1]

        # Map by position if we have exactly 5 buttons
        if len(last_group) == 5:
            return ResponseAction(
                copy_ref=last_group[0],
                regenerate_ref=last_group[1],
                like_ref=last_group[2],
                dislike_ref=last_group[3],
                share_ref=last_group[4],
            )

        # Fallback: try to identify by JS
        return self._get_response_actions_via_js()

    def _get_response_actions_via_js(self) -> ResponseAction:
        """Use JS to identify response action buttons by their SVG icons."""
        js = """
        (function() {
            // Find the last assistant message container
            const messages = document.querySelectorAll('[class*="chat-message"], [class*="ds-markdown"]');
            const lastMsg = messages[messages.length - 1];
            if (!lastMsg) return JSON.stringify({error: 'no messages'});

            const btns = lastMsg.querySelectorAll('.ds-icon-button, button');
            const refs = [];
            btns.forEach(btn => {
                refs.push(btn.className.substring(0, 30));
            });
            return JSON.stringify({count: btns.length, classes: refs});
        })()
        """
        result, code = self.a11y.eval_js(js)
        # If JS approach fails, return empty
        return ResponseAction(None, None, None, None, None)

    def copy_last_response(self) -> bool:
        """Click the copy button on the last assistant response."""
        actions = self.get_last_response_actions()
        if actions.copy_ref:
            return self.a11y.click_by_ref(actions.copy_ref)
        # JS fallback
        js = """
        (function() {
            // Find buttons in last response, first one is usually copy
            const msg = document.querySelectorAll('[class*="message"], [class*="markdown"]');
            const last = msg[msg.length - 1];
            if (!last) return 'no message';
            const btns = last.querySelectorAll('button, .ds-icon-button');
            if (btns.length > 0) { btns[0].click(); return 'clicked'; }
            return 'no buttons';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "clicked" in result

    def regenerate_last_response(self) -> bool:
        """Click the regenerate/redo button on the last assistant response."""
        actions = self.get_last_response_actions()
        if actions.regenerate_ref:
            return self.a11y.click_by_ref(actions.regenerate_ref)
        # JS fallback
        js = """
        (function() {
            const msg = document.querySelectorAll('[class*="message"], [class*="markdown"]');
            const last = msg[msg.length - 1];
            if (!last) return 'no message';
            const btns = last.querySelectorAll('button, .ds-icon-button');
            if (btns.length > 1) { btns[1].click(); return 'clicked'; }
            return 'no buttons';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "clicked" in result

    def like_last_response(self) -> bool:
        """Click the like/thumbs-up button on the last assistant response."""
        actions = self.get_last_response_actions()
        if actions.like_ref:
            return self.a11y.click_by_ref(actions.like_ref)
        # JS fallback
        js = """
        (function() {
            const msg = document.querySelectorAll('[class*="message"], [class*="markdown"]');
            const last = msg[msg.length - 1];
            if (!last) return 'no message';
            const btns = last.querySelectorAll('button, .ds-icon-button');
            if (btns.length > 2) { btns[2].click(); return 'clicked'; }
            return 'no buttons';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "clicked" in result

    def dislike_last_response(self) -> bool:
        """Click the dislike/thumbs-down button on the last assistant response."""
        actions = self.get_last_response_actions()
        if actions.dislike_ref:
            return self.a11y.click_by_ref(actions.dislike_ref)
        # JS fallback
        js = """
        (function() {
            const msg = document.querySelectorAll('[class*="message"], [class*="markdown"]');
            const last = msg[msg.length - 1];
            if (!last) return 'no message';
            const btns = last.querySelectorAll('button, .ds-icon-button');
            if (btns.length > 3) { btns[3].click(); return 'clicked'; }
            return 'no buttons';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "clicked" in result

    def share_last_response(self) -> bool:
        """Click the share button on the last assistant response."""
        actions = self.get_last_response_actions()
        if actions.share_ref:
            return self.a11y.click_by_ref(actions.share_ref)
        # JS fallback
        js = """
        (function() {
            const msg = document.querySelectorAll('[class*="message"], [class*="markdown"]');
            const last = msg[msg.length - 1];
            if (!last) return 'no message';
            const btns = last.querySelectorAll('button, .ds-icon-button');
            if (btns.length > 4) { btns[4].click(); return 'clicked'; }
            return 'no buttons';
        })()
        """
        result, code = self.a11y.eval_js(js)
        return code == 0 and "clicked" in result

    # --- Message parsing (FIXED: scoped to main chat area) ---

    def get_messages(self) -> list[Message]:
        """Extract visible messages from the current DOM.

        DeepSeek uses a virtual list that only renders visible messages.
        This returns only the messages currently in the viewport — which
        is correct for real-time observation (the latest message is
        typically visible after sending).

        For full history, use get_all_messages().
        """
        snap = self.a11y.snapshot()
        return self._parse_messages_scoped(snap)

    def get_all_messages(self) -> list[Message]:
        """Extract all messages by scrolling through the virtual list.

        Slow — only use when you need complete conversation history.
        """
        self._scroll_to_top_and_load_messages()
        snap = self.a11y.snapshot()
        return self._parse_messages_scoped(snap)

    def _scroll_to_top_and_load_messages(self, timeout: float = 5.0) -> None:
        """Scroll to the top of the chat to load all virtualized messages."""
        js = r"""
        (function() {
            const chatContainer = document.querySelector('.ds-virtual-list, [class*="virtual-list"], .ds-chat-container');
            if (chatContainer) {
                chatContainer.scrollTop = 0;
            } else {
                window.scrollTo(0, 0);
            }
            // Also try scrolling the main content area
            const main = document.querySelector('main, [class*="chat-content"], [class*="conversation"]');
            if (main) main.scrollTop = 0;
            return 'scrolled';
        })()
        """
        self.a11y.eval_js(js)
        # Wait a bit for the virtual list to render items
        time.sleep(0.5)

        # Check if we need to wait for more messages to load
        deadline = time.time() + timeout
        last_count = 0
        while time.time() < deadline:
            result, code = self.a11y.eval_js("document.querySelectorAll('.ds-message').length")
            if code == 0 and result:
                try:
                    count = int(result.strip().strip('"'))
                    if count == last_count and count > 0:
                        break  # Stabilized
                    last_count = count
                except ValueError:
                    pass
            time.sleep(0.3)

    def _parse_messages_scoped(self, snapshot: str) -> list[Message]:
        """Parse messages from the main chat area using DOM extraction.

        Falls back to a11y-tree heuristics if JS eval is unavailable.
        """
        # Primary strategy: use JS to extract clean message data from React DOM.
        # DeepSeek renders messages in .ds-message containers. User messages have
        # an additional wrapper class (e.g. d29f3d7d) before .ds-message, while
        # assistant messages have only .ds-message.
        #
        # Thinking traces: When deep thinking is on, assistant messages contain
        # .ds-think-content containers with their own .ds-markdown children.
        # The actual response is in a .ds-markdown that is NOT inside .ds-think-content.
        js = r"""
        (function() {
            const results = [];
            const msgElements = document.querySelectorAll('.ds-message');

            msgElements.forEach(el => {
                const text = el.textContent.trim();
                if (!text || text.length < 2) return;

                // Distinguish user vs assistant by extra wrapper classes.
                // User messages include an additional hash-like wrapper class.
                const classes = (el.className || '').split(/\s+/).filter(Boolean);
                const hasHashClass = classes.some(c => /^d[0-9a-f]{7,}$/i.test(c));
                const isUser = hasHashClass;

                let content = text;

                if (!isUser) {
                    // For assistant messages, find .ds-markdown that is NOT inside
                    // a .ds-think-content container (those are thinking traces).
                    const allMarkdowns = el.querySelectorAll('.ds-markdown');
                    let responseMarkdown = null;
                    allMarkdowns.forEach(md => {
                        if (!md.closest('.ds-think-content')) {
                            responseMarkdown = md;
                        }
                    });
                    if (responseMarkdown) {
                        content = responseMarkdown.textContent.trim();
                    } else {
                        // Fallback: strip thinking trace from raw textContent
                        // Pattern 1: completed thinking "已思考（用时 X 秒）"
                        const thinkMatch = text.match(/已思考[\s\S]*?秒[）)]?(.*)/);
                        if (thinkMatch) {
                            content = thinkMatch[1].trim();
                        } else {
                            // Pattern 2: in-progress thinking placeholder "正在思考"
                            content = text.replace(/^(正在思考\s*)+/, '');
                        }
                    }
                }

                results.push({
                    role: isUser ? 'user' : 'assistant',
                    content: content.substring(0, 5000)
                });
            });

            return JSON.stringify(results);
        })()
        """
        result, code = self.a11y.eval_js(js)
        if code == 0 and result:
            try:
                # eval_js returns a JSON-string-as-string literal (double-quoted).
                # Use ast.literal_eval to unescape the outer string, then json.loads.
                clean = ast.literal_eval(result.strip())
                data = json.loads(clean)
                if isinstance(data, list) and data:
                    return [Message(role=m["role"], content=m["content"]) for m in data]
            except (ValueError, json.JSONDecodeError, KeyError):
                pass

        # Fallback: a11y tree heuristic parsing
        return self._parse_messages_fallback(snapshot)

    def _parse_messages_fallback(self, snapshot: str) -> list[Message]:
        """Fallback message parser using a11y tree heuristics."""
        lines = snapshot.split("\n")
        raw_entries: list[str] = []

        # Find boundaries: sidebar ends at "开启新对话", input area starts at textbox
        sidebar_end = 0
        input_line_idx = len(lines)
        for i, line in enumerate(lines):
            if 'StaticText "开启新对话"' in line:
                sidebar_end = i + 5  # skip past the header area
            if 'textbox "给 DeepSeek' in line or 'textarea' in line:
                input_line_idx = i
                break

        chat_lines = lines[sidebar_end:input_line_idx]

        for line in chat_lines:
            text = None
            if 'StaticText "' in line:
                m = re.search(r'StaticText "([^"]+)"', line)
                if m:
                    text = m.group(1)
            elif 'text: ' in line:
                m = re.search(r'text: (.+)$', line)
                if m:
                    text = m.group(1).strip()

            if text is None:
                continue

            # Skip UI chrome and sidebar items
            if text in self.UI_CHROME or any(ui in text for ui in self.UI_CHROME):
                continue
            if text in ("昨天", "今天", "7 天内", "30 天内"):
                continue

            # Skip thinking indicator
            if self.THINKING_INDICATOR in text and "用时" in text:
                continue

            if raw_entries and raw_entries[-1] == text:
                continue
            raw_entries.append(text)

        # Without reliable DOM metadata, use stable alternating roles.
        messages: list[Message] = []
        for idx, text in enumerate(raw_entries):
            role = "user" if idx % 2 == 0 else "assistant"
            messages.append(Message(role=role, content=text))
        return messages

    # --- Thinking trace ---

    def get_thinking_trace(self) -> Optional[ThinkingTrace]:
        snap = self.a11y.snapshot()
        return self._parse_thinking(snap)

    def _parse_thinking(self, snapshot: str) -> Optional[ThinkingTrace]:
        # Primary: DOM-based extraction from .ds-think-content containers.
        js = r"""
        (function() {
            const result = {exists: false, time: null, content: null};
            const msgElements = document.querySelectorAll('.ds-message');
            let lastAssistantMsg = null;
            for (let i = msgElements.length - 1; i >= 0; i--) {
                const classes = (msgElements[i].className || '').split(/\s+/).filter(Boolean);
                const hasHashClass = classes.some(c => /^d[0-9a-f]{7,}$/i.test(c));
                if (classes.includes('ds-message') && !hasHashClass) {
                    lastAssistantMsg = msgElements[i];
                    break;
                }
            }
            if (!lastAssistantMsg) return JSON.stringify(result);

            // Look for thinking container
            const thinkContainer = lastAssistantMsg.querySelector('.ds-think-content');
            if (!thinkContainer) return JSON.stringify(result);

            result.exists = true;

            // Extract thinking time from header text (e.g., "已思考（用时 4 秒）")
            const headerEl = lastAssistantMsg.querySelector('[class*="think"], .ds-message > div:first-child');
            const fullText = lastAssistantMsg.textContent;
            const timeMatch = fullText.match(/已思考[（(]用时\s*(\d+)\s*秒[）)]/);
            if (timeMatch) {
                result.time = timeMatch[1] + ' 秒';
            }

            // Collect all thinking markdowns
            const thinkMarkdowns = thinkContainer.querySelectorAll('.ds-markdown');
            const parts = [];
            thinkMarkdowns.forEach(md => {
                const text = md.textContent.trim();
                if (text) parts.push(text);
            });
            result.content = parts.join('\n');

            return JSON.stringify(result);
        })()
        """
        result, code = self.a11y.eval_js(js)
        if code == 0 and result:
            try:
                clean = ast.literal_eval(result.strip())
                data = json.loads(clean)
                if data.get("exists") and data.get("content"):
                    return ThinkingTrace(content=data["content"], time=data.get("time"))
            except (ValueError, json.JSONDecodeError, KeyError):
                pass

        # Fallback: a11y tree heuristic parsing
        thinking_content = []
        thinking_time = None
        in_thinking = False

        for line in snapshot.split("\n"):
            text = None
            if 'StaticText "' in line:
                m = re.search(r'StaticText "([^"]+)"', line)
                if m:
                    text = m.group(1)
            elif 'text: ' in line:
                m = re.search(r'text: (.+)$', line)
                if m:
                    text = m.group(1).strip()

            if text is None:
                continue

            if self.THINKING_INDICATOR in text and "用时" in text:
                in_thinking = True
                time_match = self.THINKING_TIME_PATTERN.search(text)
                if time_match:
                    thinking_time = time_match.group(1)
                continue

            if in_thinking:
                # Collect thinking content until we hit the response
                if "We need" in text or "我们需要" in text:
                    thinking_content.append(text)
                # Stop when we see the actual response (simple heuristic)
                elif len(text) > 50 or text.isdigit():
                    break

        if thinking_content:
            return ThinkingTrace(content="".join(thinking_content), time=thinking_time)
        return None
