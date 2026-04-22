"""Layer 1 primitives: generic accessibility-tree operations via agent-browser.

This module provides the lowest-level building blocks for interacting with
a browser page through the Chrome DevTools Protocol (CDP) as exposed by
agent-browser.  It knows nothing about DeepSeek's UI semantics — only how
to query and interact with the a11y tree.
"""

import ast
import json
import os
import re
import subprocess
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Layer 0: Infrastructure
# ---------------------------------------------------------------------------

def _clean_env():
    """Return environment without proxy variables."""
    return {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}


def _run(cmd, check=False):
    """Run a command through agent-browser."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=_clean_env())
    except FileNotFoundError as exc:
        raise RuntimeError(
            "agent-browser was not found on PATH. "
            "Install it with: npm install -g agent-browser"
        ) from exc
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nstderr: {result.stderr.strip()}")
    return result.stdout, result.returncode


# ---------------------------------------------------------------------------
# Layer 1: Accessibility Tree Primitives
# ---------------------------------------------------------------------------

class A11yPrimitives:
    """Generic operations on the accessibility tree.

    These are the lowest-level building blocks. They know nothing about
    DeepSeek's UI semantics — only how to query and interact with the
    a11y tree exposed by agent-browser.
    """

    def __init__(
        self,
        session: str = "default",
        auto_connect: bool = False,
        cdp: Optional[str] = None,
        profile: Optional[str] = None,
        headed: bool = False,
    ):
        self.session = session
        self.auto_connect = auto_connect
        self._cmd_base = ["agent-browser", "--session", session]
        if cdp:
            self._cmd_base.extend(["--cdp", str(cdp)])
        elif auto_connect:
            self._cmd_base.append("--auto-connect")
        if profile:
            self._cmd_base.extend(["--profile", profile])
        if headed:
            self._cmd_base.append("--headed")

        # Caching: snapshot is expensive (subprocess call); cache briefly
        # to avoid redundant calls within a single observation cycle.
        self._snapshot_cache: Optional[tuple[str, float]] = None
        self._snapshot_ttl = 0.15
        self._url_cache: Optional[tuple[str, float]] = None
        self._url_ttl = 2.0

    def _exec(self, *args, check: bool = False):
        cmd = self._cmd_base + list(args)
        return _run(cmd, check=check)

    def invalidate_cache(self) -> None:
        """Drop cached snapshot/URL after mutating operations."""
        self._snapshot_cache = None
        self._url_cache = None

    # --- Navigation ---

    def open_url(self, url: str) -> tuple[str, int]:
        self.invalidate_cache()
        return self._exec("open", url)

    def get_url(self) -> str:
        now = time.time()
        if self._url_cache and now - self._url_cache[1] < self._url_ttl:
            return self._url_cache[0]
        out, _ = self._exec("get", "url")
        url = out.strip()
        self._url_cache = (url, now)
        return url

    # --- Snapshot queries ---

    def snapshot(self) -> str:
        now = time.time()
        if self._snapshot_cache and now - self._snapshot_cache[1] < self._snapshot_ttl:
            return self._snapshot_cache[0]
        out, _ = self._exec("snapshot")
        self._snapshot_cache = (out, now)
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
        # Role + optional text pattern:  - role "text" [ref=...]
        m = re.match(r'^\s*-\s+(\w+)(?:\s+"([^"]*)")?(?:\s+\[ref=([^\]]+)\])?', line)
        if not m:
            return None
        attrs: dict[str, str | bool] = {}
        for raw in re.findall(r"\[([^\]]+)\]", line):
            for chunk in (part.strip() for part in raw.split(",")):
                if not chunk:
                    continue
                if "=" in chunk:
                    key, value = chunk.split("=", 1)
                    attrs[key.strip()] = value.strip()
                else:
                    attrs[chunk] = True
        ref = m.group(3)
        if not ref and isinstance(attrs.get("ref"), str):
            ref = attrs["ref"]
        return {
            "role": m.group(1),
            "text": m.group(2) or "",
            "ref": ref,
            "attrs": attrs,
            "raw": line,
        }

    # --- Interaction ---

    def click_by_ref(self, ref: str) -> bool:
        self.invalidate_cache()
        _, code = self._exec("click", f"@{ref}")
        return code == 0

    def type_by_ref(self, ref: str, text: str) -> bool:
        self.invalidate_cache()
        _, code = self._exec("type", f"@{ref}", text)
        return code == 0

    def press_key(self, key: str) -> bool:
        self.invalidate_cache()
        _, code = self._exec("press", key)
        return code == 0

    def eval_js(self, script: str) -> tuple[str, int]:
        # JS eval may mutate DOM; be conservative and invalidate cache.
        self.invalidate_cache()
        return self._exec("eval", script)

    def eval_json(self, script: str) -> Optional[dict]:
        """Evaluate JS that returns JSON.stringify() and parse the result.

        Handles the common double-wrapping from agent-browser eval output.
        Returns None on any parse failure.
        """
        result, code = self.eval_js(script)
        if code != 0 or not result:
            return None
        text = result.strip()
        # Try direct JSON first (if agent-browser ever returns raw JSON)
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        # Try unwrapping a JSON-string-as-Python-literal
        try:
            clean = ast.literal_eval(text)
            if isinstance(clean, str):
                data = json.loads(clean)
                if isinstance(data, dict):
                    return data
        except (ValueError, SyntaxError, json.JSONDecodeError):
            pass
        return None

    # --- Waiting ---

    def wait_for_element(self, role: Optional[str] = None, text_contains: Optional[str] = None, timeout: float = 10, interval: float = 0.5) -> Optional[dict]:
        """Poll snapshot until element appears or timeout."""
        deadline = time.time() + timeout
        current_interval = interval
        while time.time() < deadline:
            elem = self.find_first(self.snapshot(), role=role, text_contains=text_contains)
            if elem:
                return elem
            time.sleep(current_interval)
            current_interval = min(current_interval * 1.5, 2.0)
        return None

    def wait_for_text_absent(self, text: str, timeout: float = 10, interval: float = 0.5) -> bool:
        """Wait until text disappears from snapshot (e.g. loading indicator)."""
        deadline = time.time() + timeout
        current_interval = interval
        while time.time() < deadline:
            if text not in self.snapshot():
                return True
            time.sleep(current_interval)
            current_interval = min(current_interval * 1.5, 2.0)
        return False
