"""Semantic model for chat.deepseek.com using Chrome DevTools.

Layered architecture:
- Layer 1 (A11yPrimitives): Generic accessibility-tree operations
- Layer 2 (DeepSeekSemantics): Page-specific semantic actions with fallback strategies
- Layer 3 (DeepSeekChat): High-level workflow composition for conversations
"""

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Layer 0: Infrastructure
# ---------------------------------------------------------------------------

def _clean_env():
    """Return environment without proxy variables."""
    return {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}


def _run(cmd, check=False):
    """Run a command through agent-browser."""
    result = subprocess.run(cmd, capture_output=True, text=True, env=_clean_env())
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nstderr: {result.stderr.strip()}")
    return result.stdout, result.returncode


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ChatMode(str, Enum):
    QUICK = "快速模式"
    EXPERT = "专家模式"


@dataclass
class Message:
    role: str
    content: str


@dataclass
class ThinkingTrace:
    content: str
    time: Optional[str]


@dataclass
class Conversation:
    """A conversation entry from the sidebar."""
    title: str
    ref: Optional[str]
    is_active: bool = False


@dataclass
class PageState:
    """Structured page state for state-machine transitions."""
    url: str
    is_initial_page: bool
    has_input: bool
    is_streaming: bool
    message_count: int
    mode: str
    deep_thinking_enabled: bool
    web_search_enabled: bool


@dataclass
class ResponseAction:
    """Action buttons attached to an assistant response."""
    copy_ref: Optional[str]
    regenerate_ref: Optional[str]
    like_ref: Optional[str]
    dislike_ref: Optional[str]
    share_ref: Optional[str]


# ---------------------------------------------------------------------------
# Layer 1: Accessibility Tree Primitives
# ---------------------------------------------------------------------------

class A11yPrimitives:
    """Generic operations on the accessibility tree.

    These are the lowest-level building blocks. They know nothing about
    DeepSeek's UI semantics — only how to query and interact with the
    a11y tree exposed by agent-browser.
    """

    def __init__(self, session: str = "default", auto_connect: bool = False):
        self.session = session
        self.auto_connect = auto_connect
        self._cmd_base = ["agent-browser", "--session", session]
        if auto_connect:
            self._cmd_base.append("--auto-connect")

    def _exec(self, *args, check: bool = False):
        cmd = self._cmd_base + list(args)
        return _run(cmd, check=check)

    # --- Navigation ---

    def open_url(self, url: str) -> tuple[str, int]:
        return self._exec("open", url)

    def get_url(self) -> str:
        out, _ = self._exec("get", "url")
        return out.strip()

    # --- Snapshot queries ---

    def snapshot(self) -> str:
        out, _ = self._exec("snapshot")
        return out

    def find_elements(self, snapshot: str, role: Optional[str] = None, text_contains: Optional[str] = None) -> list[dict]:
        """Parse snapshot lines into structured element descriptors."""
        elements = []
        for line in snapshot.split("\n"):
            elem = self._parse_line(line)
            if not elem:
                continue
            if role and elem.get("role") != role:
                continue
            if text_contains and text_contains not in elem.get("text", ""):
                continue
            elements.append(elem)
        return elements

    def find_first(self, snapshot: str, role: Optional[str] = None, text_contains: Optional[str] = None) -> Optional[dict]:
        elems = self.find_elements(snapshot, role=role, text_contains=text_contains)
        return elems[0] if elems else None

    def _parse_line(self, line: str) -> Optional[dict]:
        """Parse a snapshot line like:  - heading "Hello" [ref=abc123]"""
        # Role + text pattern:  - role "text" [ref=...]
        m = re.match(r'^\s*-\s+(\w+)\s+"([^"]*)"(?:\s+\[ref=([^\]]+)\])?', line)
        if not m:
            return None
        return {
            "role": m.group(1),
            "text": m.group(2),
            "ref": m.group(3),
            "raw": line,
        }

    # --- Interaction ---

    def click_by_ref(self, ref: str) -> bool:
        _, code = self._exec("click", f"@{ref}")
        return code == 0

    def type_by_ref(self, ref: str, text: str) -> bool:
        _, code = self._exec("type", f"@{ref}", text)
        return code == 0

    def press_key(self, key: str) -> bool:
        _, code = self._exec("press", key)
        return code == 0

    def eval_js(self, script: str) -> tuple[str, int]:
        return self._exec("eval", script)

    # --- Waiting ---

    def wait_for_element(self, role: Optional[str] = None, text_contains: Optional[str] = None, timeout: float = 10, interval: float = 0.5) -> Optional[dict]:
        """Poll snapshot until element appears or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            elem = self.find_first(self.snapshot(), role=role, text_contains=text_contains)
            if elem:
                return elem
            time.sleep(interval)
        return None

    def wait_for_text_absent(self, text: str, timeout: float = 10, interval: float = 0.5) -> bool:
        """Wait until text disappears from snapshot (e.g. loading indicator)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if text not in self.snapshot():
                return True
            time.sleep(interval)
        return False


# ---------------------------------------------------------------------------
# Layer 2: DeepSeek Semantic Actions
# ---------------------------------------------------------------------------

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

    THINKING_INDICATOR = "已思考"
    THINKING_TIME_PATTERN = re.compile(r"用时\s*([^）]+)")

    def __init__(self, layer1: A11yPrimitives):
        self.a11y = layer1

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
                web_search: false
            };

            // Initial page: has a radiogroup with 2+ radios AND textarea AND no messages
            const radios = document.querySelectorAll('[role="radiogroup"] radio, [role="radio"]');
            const textarea = document.querySelector('textarea');
            const messages = document.querySelectorAll('.ds-message');
            if (radios.length >= 2 && textarea && messages.length === 0) {
                info.is_initial_page = true;
            }

            // Has input field
            info.has_input = !!textarea;

            // Streaming: look for loading spinner or generating indicator
            const spinner = document.querySelector('.ds-loading, [class*="loading"], [class*="spinner"]');
            const streamingText = document.querySelector('[class*="streaming"], [class*="generating"]');
            info.is_streaming = !!(spinner || streamingText);

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
            const radios = document.querySelectorAll('[role="radio"], input[type="radio"]');
            for (const radio of radios) {
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
            if (!info.mode) {
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
            if (!info.mode) {
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
        dom_info = {"is_initial_page": False, "has_input": False, "is_streaming": False,
                    "deep_thinking": False, "web_search": False}
        result, code = self.a11y.eval_js(js)
        if code == 0 and result:
            try:
                import ast, json
                clean = ast.literal_eval(result.strip())
                dom_info = json.loads(clean)
            except (ValueError, json.JSONDecodeError):
                pass

        # Fallback to a11y text heuristics if JS fails
        is_initial = dom_info.get("is_initial_page", False)
        if not is_initial:
            is_initial = "radiogroup" in snap and ('radio "快速模式"' in snap or 'radio "Instant"' in snap)

        has_input = dom_info.get("has_input", False) or ("textarea" in snap or "textbox" in snap)
        is_streaming = dom_info.get("is_streaming", False) or ("正在思考" in snap or "generating" in snap.lower())
        deep_thinking = dom_info.get("deep_thinking", False) or ('button "深度思考"' in snap or 'button "DeepThink"' in snap)
        web_search = dom_info.get("web_search", False) or ('button "智能搜索"' in snap or 'button "Search"' in snap)

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
                    import ast
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

    # --- Session / Sidebar management ---

    def new_conversation(self) -> bool:
        """Start a new conversation by clicking the sidebar new-chat button."""
        # Strategy 1: Click by the "开启新对话" text which is near the button
        snap = self.a11y.snapshot()
        for line in snap.split("\n"):
            if 'StaticText "开启新对话"' in line:
                # The button is typically the sibling before this text
                return self._try_click_new_chat_via_js()
        # Strategy 2: Direct JS
        return self._try_click_new_chat_via_js()

    def _try_click_new_chat_via_js(self) -> bool:
        js = """
        (function() {
            // Find by aria-label or icon title
            const btn = document.querySelector('button[aria-label="\\u5f00\\u542f\\u65b0\\u5bf9\\u8bdd"]')
                || document.querySelector('[title="\\u5f00\\u542f\\u65b0\\u5bf9\\u8bdd"]')
                || document.querySelector('div[aria-label="\\u5f00\\u542f\\u65b0\\u5bf9\\u8bdd"]')
                || document.querySelector('[class*="new"] button');
            if (btn) { btn.click(); return 'clicked'; }
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
                import time
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
            import time
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
        if (parent && parent.textContent.includes(%s)) {
            div.click();
            return 'clicked';
        }
    }
    return 'not found';
})()
""" % json.dumps(label)
        result, code = self.a11y.eval_js(js)
        return code == 0 and "not found" not in result

    def _try_click_mode_via_js(self, label: str) -> bool:
        """Strategy 3: Broader JS search by text content."""
        js = """
(function() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    let node;
    while (node = walker.nextNode()) {
        if (node.textContent.trim() === %s) {
            const clickable = node.closest('label, button, div[role="radio"]') || node;
            clickable.click();
            return 'clicked';
        }
    }
    return 'not found';
})()
""" % json.dumps(label)
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
        """Upload a file by triggering the file input.

        Uses JS to trigger the hidden file input element.
        """
        if not os.path.exists(filepath):
            return False
        abs_path = os.path.abspath(filepath)
        js = f"""
        (function() {{
            // Find file input (may be hidden)
            let input = document.querySelector('input[type="file"]');
            if (!input) {{
                // Trigger the upload button first to reveal input
                const uploadBtn = document.querySelector('.f02f0e25')
                    || Array.from(document.querySelectorAll('.ds-icon-button'))
                        .find(b => !b.textContent.trim() && !b.disabled);
                if (uploadBtn) {{
                    uploadBtn.click();
                    // Wait a tick for input to appear
                    return 'clicked_upload';
                }}
                return 'no upload mechanism found';
            }}
            return 'found_input';
        }})()
        """
        result, code = self.a11y.eval_js(js)
        if code != 0:
            return False

        # Now set the file value using JS
        js2 = f"""
        (function() {{
            const input = document.querySelector('input[type="file"]');
            if (!input) return 'no input';
            // Create a DataTransfer object to simulate file selection
            const dt = new DataTransfer();
            // We cannot directly set files from local path due to security
            // Instead, trigger the input for manual selection or use a different approach
            input.dispatchEvent(new Event('click', {{ bubbles: true }}));
            return 'triggered';
        }})()
        """
        result2, code2 = self.a11y.eval_js(js2)
        # File upload via CDP is tricky — this triggers the dialog
        # Full implementation would need CDP's DOM.setFileInputFiles
        return code2 == 0

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

        # Find the last group of buttons that appears after assistant content.
        # Buttons in the same group share the same indentation level and are
        # siblings (they may have image children on subsequent lines).
        button_groups = []
        current_group = []
        current_indent = None

        for line in lines:
            m = re.match(r'^(\s+)-\s+button\s+\[ref=([^\]]+)\]', line)
            if m:
                indent = len(m.group(1))
                ref = m.group(2)
                if current_indent is not None and indent == current_indent:
                    # Same group (sibling button)
                    current_group.append(ref)
                else:
                    # New group
                    if len(current_group) >= 3:
                        button_groups.append(current_group)
                    current_group = [ref]
                    current_indent = indent
            else:
                # Non-button line — only reset group if indentation decreases
                # (we're moving out of the button container). Keep group if
                # we're seeing child elements like "image" at deeper indent.
                pass

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
        import time
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
                // User messages have a non-ds-message first class; assistant start with ds-message.
                const classes = (el.className || '').split(/\s+/).filter(Boolean);
                const isUser = classes[0] !== 'ds-message';

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
                import ast, json
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
        messages = []
        buffer = []
        last_was_user = False

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
                if buffer and last_was_user:
                    messages.append(Message(role="user", content="".join(buffer)))
                    buffer = []
                last_was_user = False
                continue

            # Simple heuristic: very short numeric/text answers are assistant
            # Questions ending in ? are user
            is_user = text.endswith('?') or text.endswith('？')

            if is_user and not last_was_user:
                if buffer:
                    messages.append(Message(role="assistant", content="".join(buffer)))
                    buffer = []
                last_was_user = True
            elif not is_user and last_was_user:
                if buffer:
                    messages.append(Message(role="user", content="".join(buffer)))
                    buffer = []
                last_was_user = False

            buffer.append(text)

        if buffer:
            role = "user" if last_was_user else "assistant"
            messages.append(Message(role=role, content="".join(buffer)))

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

            // Find the last assistant message with thinking content
            const msgElements = document.querySelectorAll('.ds-message');
            let lastAssistantMsg = null;
            for (let i = msgElements.length - 1; i >= 0; i--) {
                const classes = (msgElements[i].className || '').split(/\s+/).filter(Boolean);
                if (classes[0] === 'ds-message') {
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
                import ast, json
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
        self._last_message_count = len(self.get_messages())
        last_streaming_check = False

        while time.time() < deadline:
            state = self.semantics.get_page_state()
            current_count = state.message_count

            # New message arrived
            if current_count > self._last_message_count:
                self._last_message_count = current_count
                msgs = self.get_messages()
                return msgs[-1].content if msgs else None

            # If streaming, wait longer without counting against timeout as aggressively
            if state.is_streaming:
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
            state = self.semantics.get_page_state()
            if state.is_initial_page or state.has_input:
                return True
            time.sleep(0.5)
        return False


# ---------------------------------------------------------------------------
# Layer 3b: Multi-Round Conversation Manager
# ---------------------------------------------------------------------------

@dataclass
class ChatTurn:
    """A single turn in a multi-round conversation."""
    user_message: str
    assistant_response: str
    thinking_trace: Optional[ThinkingTrace] = None
    timestamp: float = field(default_factory=time.time)


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

        # Step 2: Send the message.
        if not self.chat.send_message(message):
            return None

        # Step 3: Wait for streaming to start (model picked up the message).
        if on_stream_start:
            stream_detected = self._wait_for_streaming(timeout=10)
            if stream_detected:
                on_stream_start()

        # Step 4: Wait for streaming to finish and capture response.
        response = self._wait_for_complete_response(timeout=timeout)

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
        while time.time() < deadline:
            state = self.chat.semantics.get_page_state()
            if not state.is_streaming and state.has_input:
                return True
            time.sleep(0.5)
        return False

    def _wait_for_complete_response(self, timeout: float = 120) -> Optional[str]:
        """Wait until streaming starts, then finishes, and return response.

        More reliable than message-count comparison because it uses the
        page's streaming state directly.
        """
        deadline = time.time() + timeout

        # Phase 1: Wait for streaming to start (model picked up the message)
        streaming_started = False
        while time.time() < deadline:
            state = self.chat.semantics.get_page_state()
            if state.is_streaming:
                streaming_started = True
                break
            time.sleep(0.3)

        if not streaming_started:
            # Model may have responded instantly; capture what we have
            msgs = self.chat.get_messages()
            if msgs and msgs[-1].role == "assistant":
                return msgs[-1].content
            return None

        # Phase 2: Wait for streaming to finish
        while time.time() < deadline:
            state = self.chat.semantics.get_page_state()
            if not state.is_streaming:
                # Give DOM one more moment to settle
                time.sleep(0.5)
                msgs = self.chat.get_messages()
                if msgs and msgs[-1].role == "assistant":
                    return msgs[-1].content
            time.sleep(0.5)

        return None

    def _wait_for_streaming(self, timeout: float = 5) -> bool:
        """Quick poll to detect if model started streaming."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.chat.semantics.get_page_state()
            if state.is_streaming:
                return True
            time.sleep(0.3)
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

        if not self.chat.send_message(message):
            return None

        deadline = time.time() + timeout
        last_content = ""
        response_started = False

        while time.time() < deadline:
            state = self.chat.semantics.get_page_state()

            if state.is_streaming or response_started:
                response_started = True
                msgs = self.chat.get_messages()
                if msgs and msgs[-1].role == "assistant":
                    current = msgs[-1].content
                    if len(current) > len(last_content):
                        delta = current[len(last_content):]
                        if on_token:
                            on_token(delta, current)
                        last_content = current

            if response_started and not state.is_streaming:
                # Streaming finished, do one final capture
                msgs = self.chat.get_messages()
                if msgs and msgs[-1].role == "assistant":
                    final = msgs[-1].content
                    trace = self.chat.get_thinking_trace()
                    turn = ChatTurn(
                        user_message=message,
                        assistant_response=final,
                        thinking_trace=trace,
                    )
                    self.history.append(turn)
                    return turn

            time.sleep(poll_interval)

        # Timeout — capture whatever we have
        msgs = self.chat.get_messages()
        if msgs and msgs[-1].role == "assistant":
            final = msgs[-1].content
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

    def export_markdown(self) -> str:
        """Export conversation as Markdown."""
        lines = []
        for i, turn in enumerate(self.history, 1):
            lines.append(f"## Turn {i}")
            lines.append(f"\n**User:** {turn.user_message}\n")
            if turn.thinking_trace:
                lines.append(f"\n*Thinking ({turn.thinking_trace.time}):*")
                lines.append(f"```\n{turn.thinking_trace.content}\n```\n")
            lines.append(f"\n**Assistant:** {turn.assistant_response}\n")
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
