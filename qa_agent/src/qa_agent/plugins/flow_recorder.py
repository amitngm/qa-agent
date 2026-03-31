"""
Flow Recorder — launches a headed browser, injects recorder.js, and captures
user actions into a list of YAML-compatible step dicts.

Usage (via recorder_routes.py):
    session = RecordingSession(...)
    session.start()          # opens browser, blocks in background thread
    session.get_events()     # returns captured steps so far
    session.add_assertion(kind, params)  # manually inject assertion
    session.stop()           # flushes, generates YAML, closes browser
    yaml_text = session.to_yaml()
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

# JS recorder script path
_RECORDER_JS = Path(__file__).resolve().parents[1] / "static" / "recorder.js"

# Where generated flows are saved
def _flows_dir() -> Path:
    import os
    env = os.environ.get("QA_AGENT_CONFIG_PATH")
    if env:
        return Path(env).expanduser().resolve().parent / "flows"
    return Path(__file__).resolve().parents[3] / "config" / "flows"


# ── Step deduplication ────────────────────────────────────────────────────────

def _dedup_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge consecutive fill events for the same selector (keep last value only),
    and remove duplicate clicks on the same selector in a row.
    """
    out: List[Dict[str, Any]] = []
    for step in steps:
        if not out:
            out.append(step)
            continue
        prev = out[-1]
        # Merge fill: same selector → replace text with latest
        if (step.get("op") == "interact" and step.get("action") == "fill"
                and prev.get("op") == "interact" and prev.get("action") == "fill"
                and step.get("selector") == prev.get("selector")):
            out[-1] = step
            continue
        # Skip duplicate consecutive click on same selector
        if (step.get("op") == "interact" and step.get("action") == "click"
                and prev.get("op") == "interact" and prev.get("action") == "click"
                and step.get("selector") == prev.get("selector")):
            continue
        out.append(step)
    return out


# ── Variable extraction ───────────────────────────────────────────────────────

_KNOWN_VARS = {
    "username": ["username", "user", "email", "login"],
    "password": ["password"],
}

def _maybe_variable(step: Dict[str, Any], seen_vars: Dict[str, str]) -> Dict[str, Any]:
    """Replace fill text with {{variable}} if it looks like a known credential."""
    if step.get("isPassword"):
        step = {**step, "text": "{{password}}"}
        return step
    # Check selector / label hints for username
    sel = (step.get("selector") or "").lower()
    lbl = (step.get("label") or "").lower()
    hint = sel + " " + lbl
    for var_name, keywords in _KNOWN_VARS.items():
        if any(kw in hint for kw in keywords):
            step = {**step, "text": "{{" + var_name + "}}"}
            return step
    # First seen unique value → register as a variable
    text = step.get("text") or ""
    if text and text not in seen_vars:
        var_key = re.sub(r"[^a-z0-9_]", "_", (step.get("label") or "value").lower())[:20].strip("_")
        var_key = var_key or f"var_{len(seen_vars) + 1}"
        if var_key in seen_vars.values():
            var_key = f"{var_key}_{len(seen_vars) + 1}"
        seen_vars[text] = var_key
    if text in seen_vars:
        step = {**step, "text": "{{" + seen_vars[text] + "}}"}
    return step


# ── Auto assertion generation ─────────────────────────────────────────────────

def _make_assert_url(url: str, label: str) -> Dict[str, Any]:
    path = urlparse(url).path or "/"
    # Use last 2 path segments for the contains check
    parts = [p for p in path.split("/") if p]
    contains = "/" + "/".join(parts[-2:]) if len(parts) >= 2 else "/" + "/".join(parts) if parts else "/"
    return {
        "op": "assert_url",
        "contains": contains,
        "label": f"Assert URL: {contains}",
        "_auto": True,
    }


# ── Recording session ─────────────────────────────────────────────────────────

class RecordingSession:
    def __init__(
        self,
        session_id: str,
        flow_name: str,
        start_url: str,
        username: str = "",
        password: str = "",
        browser: str = "chromium",
    ) -> None:
        self.session_id = session_id
        self.flow_name = flow_name
        self.start_url = start_url.strip()
        self.username = username
        self.password = password
        self.browser_name = browser

        self.status: str = "idle"   # idle | recording | stopped | error
        self.error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.stopped_at: Optional[str] = None

        self._steps: List[Dict[str, Any]] = []   # raw captured steps
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Playwright objects (set in thread)
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None

        # YAML output (set after stop)
        self._yaml_text: Optional[str] = None
        self._saved_path: Optional[str] = None

    # ── Public API ────────────────────────────────────────────────

    def start(self) -> None:
        self.status = "recording"
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def get_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._steps)

    def add_assertion(self, kind: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Manually insert an assertion step at the current position."""
        step: Dict[str, Any] = {"op": kind, **params, "_manual": True}
        with self._lock:
            self._steps.append(step)
        return step

    def stop(self) -> str:
        """Stop recording, generate YAML, return file path."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self.status = "stopped"
        self.stopped_at = datetime.now(timezone.utc).isoformat()
        self._yaml_text = self._generate_yaml()
        self._saved_path = self._save_yaml()
        return self._saved_path or ""

    def to_yaml(self) -> str:
        return self._yaml_text or self._generate_yaml()

    def saved_path(self) -> Optional[str]:
        return self._saved_path

    # ── Browser thread ────────────────────────────────────────────

    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
            recorder_js = _RECORDER_JS.read_text(encoding="utf-8") if _RECORDER_JS.is_file() else ""

            with sync_playwright() as pw:
                self._pw = pw
                launcher = getattr(pw, self.browser_name, pw.chromium)
                self._browser = launcher.launch(headless=False)
                context = self._browser.new_context(ignore_https_errors=True)
                if recorder_js:
                    context.add_init_script(recorder_js)
                self._page = context.new_page()
                page = self._page

                # Track navigations
                last_url: str = ""

                def on_frame_navigated(frame: Any) -> None:
                    nonlocal last_url
                    try:
                        if frame.parent_frame is not None:
                            return  # ignore sub-frames
                        url = frame.url or ""
                        if url == last_url or url.startswith("about:") or url.startswith("data:"):
                            return
                        last_url = url
                        with self._lock:
                            self._steps.append({
                                "op": "navigate",
                                "url": url,
                                "wait_until": "domcontentloaded",
                                "label": f"Navigate to {urlparse(url).path or '/'}",
                            })
                            # Auto-assert URL after navigation
                            self._steps.append(_make_assert_url(url, "Auto assert URL"))
                    except Exception:
                        pass

                page.on("framenavigated", on_frame_navigated)

                # Navigate to start URL
                try:
                    page.goto(self.start_url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as exc:
                    logger.warning("recorder: initial navigate failed: %s", exc)

                # Poll JS event buffer
                while not self._stop_event.is_set():
                    try:
                        events = page.evaluate(
                            "() => { const e = window.__qa_events || []; window.__qa_events = []; return e; }"
                        )
                        if events and isinstance(events, list):
                            with self._lock:
                                for ev in events:
                                    if isinstance(ev, dict):
                                        ev.pop("ts", None)
                                        self._steps.append(ev)
                    except Exception:
                        pass
                    time.sleep(0.5)

                # Final drain
                try:
                    events = page.evaluate(
                        "() => { const e = window.__qa_events || []; window.__qa_events = []; return e; }"
                    )
                    if events and isinstance(events, list):
                        with self._lock:
                            for ev in events:
                                if isinstance(ev, dict):
                                    ev.pop("ts", None)
                                    self._steps.append(ev)
                except Exception:
                    pass

                try:
                    context.close()
                    self._browser.close()
                except Exception:
                    pass

        except Exception as exc:
            logger.error("recorder: session %s error: %s", self.session_id, exc)
            self.status = "error"
            self.error = str(exc)

    # ── YAML generation ───────────────────────────────────────────

    def _generate_yaml(self) -> str:
        with self._lock:
            raw_steps = list(self._steps)

        deduped = _dedup_steps(raw_steps)

        # Replace credential values with variables
        seen_vars: Dict[str, str] = {}
        processed: List[Dict[str, Any]] = []
        for step in deduped:
            if step.get("op") == "interact" and step.get("action") in ("fill", "select"):
                step = _maybe_variable(dict(step), seen_vars)
            processed.append(step)

        # Build step dicts for YAML
        yaml_steps = []
        step_idx = 0
        for step in processed:
            step_idx += 1
            op = step.get("op", "")
            clean: Dict[str, Any] = {}

            if op == "navigate":
                url = step.get("url") or ""
                # Replace base origin with {{base_url}}
                try:
                    parsed = urlparse(url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    start_base = urlparse(self.start_url)
                    start_base_s = f"{start_base.scheme}://{start_base.netloc}"
                    if base == start_base_s:
                        url = "{{base_url}}" + (parsed.path or "") + (
                            "?" + parsed.query if parsed.query else "")
                except Exception:
                    pass
                clean = {
                    "key": f"step_{step_idx:03d}",
                    "op": "navigate",
                    "url": url,
                    "wait_until": "domcontentloaded",
                    "label": step.get("label") or f"Navigate {step_idx}",
                }

            elif op == "interact":
                clean = {
                    "key": f"step_{step_idx:03d}",
                    "op": "interact",
                    "action": step.get("action", "click"),
                    "selector": step.get("selector", ""),
                    "label": step.get("label") or f"Interact {step_idx}",
                }
                if step.get("action") in ("fill", "select"):
                    clean["text"] = step.get("text", "")

            elif op == "assert_url":
                clean = {
                    "key": f"step_{step_idx:03d}",
                    "op": "assert_url",
                    "contains": step.get("contains", ""),
                    "label": step.get("label") or "Assert URL",
                }
                if step.get("_auto"):
                    clean["optional"] = True

            elif op == "assert_visible":
                clean = {
                    "key": f"step_{step_idx:03d}",
                    "op": "assert_visible",
                    "selector": step.get("selector", ""),
                    "label": step.get("label") or "Assert visible",
                }

            elif op == "assert_text":
                clean = {
                    "key": f"step_{step_idx:03d}",
                    "op": "assert_text",
                    "selector": step.get("selector", ""),
                    "contains": step.get("contains", ""),
                    "label": step.get("label") or "Assert text",
                }

            else:
                clean = {"key": f"step_{step_idx:03d}", **{k: v for k, v in step.items()
                                                           if k not in ("_auto", "_manual", "ts", "isPassword")}}

            # Remove internal keys
            clean.pop("_auto", None)
            clean.pop("_manual", None)
            clean.pop("isPassword", None)
            clean.pop("inputType", None)
            yaml_steps.append(clean)

        # Build parameters from seen_vars
        parameters = []
        for raw_val, var_key in seen_vars.items():
            parameters.append({"name": var_key, "type": "string", "default": raw_val})
        # Always include base_url, username, password
        for name in ("base_url", "username", "password"):
            if not any(p["name"] == name for p in parameters):
                default = self.start_url if name == "base_url" else (self.username if name == "username" else "")
                parameters.append({"name": name, "type": "string", "default": default})

        flow_key = re.sub(r"[^a-z0-9_-]", "-", self.flow_name.lower()).strip("-") or "recorded-flow"
        doc: Dict[str, Any] = {
            "flow_key": flow_key,
            "flow_version": "1.0.0",
            "description": f"Recorded flow: {self.flow_name}",
            "recorded_at": self.started_at or datetime.now(timezone.utc).isoformat(),
            "source": "recorded",
            "browser": self.browser_name,
            "headless": False,
            "parameters": parameters,
            "steps": yaml_steps,
        }

        return yaml.dump(doc, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def _save_yaml(self) -> Optional[str]:
        try:
            flows_dir = _flows_dir()
            flows_dir.mkdir(parents=True, exist_ok=True)
            flow_key = re.sub(r"[^a-z0-9_-]", "-", self.flow_name.lower()).strip("-") or "recorded-flow"
            path = flows_dir / f"{flow_key}.yaml"
            path.write_text(self._yaml_text or "", encoding="utf-8")
            logger.info("recorder: saved flow to %s", path)
            return str(path)
        except Exception as exc:
            logger.error("recorder: failed to save YAML: %s", exc)
            return None
