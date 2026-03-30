"""Playwright-backed :class:`~qa_agent.platform.driver.PlatformDriver` — generic, config-driven ops."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from qa_agent.platform.driver import NavigateTarget
from qa_agent.platform.types import DriverResult

logger = logging.getLogger(__name__)


def _as_str(v: Any) -> str:
    return v if isinstance(v, str) else str(v)


def _nth_locator(page: Any, selector: str, action: Mapping[str, Any]) -> Any:
    loc = page.locator(_as_str(selector))
    nth = action.get("nth")
    if nth is not None:
        loc = loc.nth(int(nth))
    return loc


class PlaywrightPlatformDriver:
    """
    Sync Playwright implementation of the four platform operations.

    Parameters in maps are intentionally generic (selectors, URLs, keys); the host
    YAML encodes app-specific flows — this class only interprets operation *kinds*.
    """

    def __init__(
        self,
        *,
        browser: str = "chromium",
        headless: bool = True,
        ignore_https_errors: bool = True,
    ) -> None:
        self._browser_name = (browser or "chromium").lower()
        self._headless = headless
        self._ignore_https_errors = ignore_https_errors
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def start(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launcher = getattr(self._pw, self._browser_name, self._pw.chromium)
        self._browser = launcher.launch(headless=self._headless)
        logger.info(
            "Playwright: browser launched channel=%s headless=%s",
            self._browser_name,
            self._headless,
        )

    def close(self) -> None:
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._pw is not None:
            self._pw.stop()
            self._pw = None

    def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page
        if self._browser is None:
            raise RuntimeError("PlaywrightPlatformDriver.start() was not called")
        self._context = self._browser.new_context(ignore_https_errors=self._ignore_https_errors)
        logger.info(
            "Playwright: new_context(ignore_https_errors=%s)",
            self._ignore_https_errors,
        )
        self._page = self._context.new_page()
        return self._page

    def get_page(self) -> Any:
        """Return the active Playwright page (after :meth:`start`)."""
        return self._ensure_page()

    def navigate(self, target: NavigateTarget) -> DriverResult:
        url_for_log = "?"
        try:
            page = self._ensure_page()
            if isinstance(target, str):
                url = target
                extra: Mapping[str, Any] = {}
            else:
                url = target.get("url") or target.get("goto")
                if not url:
                    return DriverResult(
                        ok=False,
                        errors=("navigate target must include url or goto string",),
                        detail={"target_keys": list(target.keys())},
                    )
                extra = dict(target)
            wait_until = extra.get("wait_until", "load")
            timeout_ms = extra.get("timeout_ms")
            kwargs: dict[str, Any] = {"wait_until": wait_until}
            if timeout_ms is not None:
                kwargs["timeout"] = float(timeout_ms)
            u = _as_str(url)
            url_for_log = u
            logger.info("Playwright: page.goto start url=%s wait_until=%s", u, wait_until)
            page.goto(u, **kwargs)
            logger.info("Playwright: page.goto finished ok url=%s", u)
            return DriverResult(
                ok=True,
                detail={"url": u, "wait_until": wait_until},
            )
        except Exception as exc:  # noqa: BLE001 — driver boundary
            logger.warning(
                "Playwright: page.goto failed url=%s err=%s",
                url_for_log,
                exc,
            )
            return DriverResult(
                ok=False,
                errors=(str(exc),),
                detail={"exception_type": type(exc).__name__, "url": url_for_log},
            )

    def interact(self, action: Mapping[str, Any]) -> DriverResult:
        try:
            page = self._ensure_page()
            kind = (action.get("action") or action.get("type") or "").lower()
            selector = action.get("selector")
            if not kind:
                return DriverResult(ok=False, errors=("interact requires action or type",), detail={})
            if not selector and kind not in ("press", "keyboard"):
                return DriverResult(ok=False, errors=("interact requires selector for this action",), detail={})

            tm = action.get("timeout_ms")
            timeout = float(tm) if tm is not None else None

            if kind == "click":
                _nth_locator(page, _as_str(selector), action).click(timeout=timeout)
            elif kind == "fill":
                _nth_locator(page, _as_str(selector), action).fill(
                    _as_str(action.get("text", "")),
                    timeout=timeout,
                )
            elif kind == "press":
                key = _as_str(action.get("key", "Enter"))
                if selector:
                    _nth_locator(page, _as_str(selector), action).press(key, timeout=timeout)
                else:
                    page.keyboard.press(key)
            elif kind == "dblclick":
                _nth_locator(page, _as_str(selector), action).dblclick(timeout=timeout)
            elif kind == "check":
                _nth_locator(page, _as_str(selector), action).check(timeout=timeout)
            elif kind == "uncheck":
                _nth_locator(page, _as_str(selector), action).uncheck(timeout=timeout)
            elif kind == "hover":
                _nth_locator(page, _as_str(selector), action).hover(timeout=timeout)
            else:
                return DriverResult(
                    ok=False,
                    errors=(f"unsupported interact action: {kind}",),
                    detail={"supported": ["click", "fill", "press", "dblclick", "check", "uncheck", "hover"]},
                )

            return DriverResult(ok=True, detail={"action": kind, "selector": selector})
        except Exception as exc:  # noqa: BLE001
            return DriverResult(ok=False, errors=(str(exc),), detail={"exception_type": type(exc).__name__})

    def read(self, spec: Mapping[str, Any]) -> DriverResult:
        try:
            page = self._ensure_page()
            selector = spec.get("selector")
            prop = (spec.get("property") or spec.get("prop") or "inner_text").lower()
            if not selector and not spec.get("evaluate"):
                return DriverResult(ok=False, errors=("read requires selector or evaluate",), detail={})

            if spec.get("evaluate"):
                raw = page.evaluate(_as_str(spec["evaluate"]))
                return DriverResult(ok=True, detail={"evaluate": True, "value": raw})

            loc = page.locator(_as_str(selector))
            if prop in ("inner_text", "innertext"):
                value: Any = loc.inner_text(timeout=spec.get("timeout_ms"))
            elif prop in ("text_content", "textcontent"):
                value = loc.text_content(timeout=spec.get("timeout_ms"))
            elif prop in ("input_value", "value"):
                value = loc.input_value(timeout=spec.get("timeout_ms"))
            elif prop == "is_visible":
                value = loc.is_visible(timeout=spec.get("timeout_ms"))
            elif prop == "is_enabled":
                value = loc.is_enabled(timeout=spec.get("timeout_ms"))
            else:
                return DriverResult(
                    ok=False,
                    errors=(f"unsupported read property: {prop}",),
                    detail={},
                )
            return DriverResult(ok=True, detail={"property": prop, "selector": selector, "value": value})
        except Exception as exc:  # noqa: BLE001
            return DriverResult(ok=False, errors=(str(exc),), detail={"exception_type": type(exc).__name__})

    def wait(self, spec: Mapping[str, Any]) -> DriverResult:
        try:
            page = self._ensure_page()
            load_state = spec.get("load_state")
            if load_state:
                timeout_ms = float(spec.get("timeout_ms", 45_000))
                page.wait_for_load_state(_as_str(load_state), timeout=timeout_ms)
                return DriverResult(ok=True, detail={"load_state": load_state})

            selector = spec.get("selector")
            if not selector:
                timeout_ms = float(spec.get("timeout_ms", 30_000))
                page.wait_for_timeout(timeout_ms)
                return DriverResult(ok=True, detail={"wait": "timeout_ms", "duration_ms": timeout_ms})

            state = spec.get("state", "visible")
            timeout_ms = spec.get("timeout_ms", 30_000)
            page.wait_for_selector(_as_str(selector), state=state, timeout=float(timeout_ms))
            return DriverResult(ok=True, detail={"selector": selector, "state": state})
        except Exception as exc:  # noqa: BLE001
            return DriverResult(ok=False, errors=(str(exc),), detail={"exception_type": type(exc).__name__})
