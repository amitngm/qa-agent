"""Generic URL login + same-origin safe link exploration (Playwright)."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, List, Mapping, MutableMapping, Optional, Set
from urllib.parse import urlparse, urljoin

from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.platform.auto_explore_models import (
    AutoExploreSummary,
    LoginDetectionResult,
    PageExploreResult,
    SkippedAction,
)
from qa_agent.platform.login_detection import perform_login_with_detection
from qa_agent.platform.playwright_driver import PlaywrightPlatformDriver
from qa_agent.store.file_store import FileRunStore
from qa_agent.validation.categories import ValidationCategory

logger = logging.getLogger(__name__)

# Temporary: after first successful navigation, block so headed browsers stay open long enough to see the page.
# Set to 0 to disable. Remove or gate behind env once workflows are stable.
POST_NAV_DEBUG_PAUSE_MS = 5000

RISKY_SUBSTRINGS = (
    "delete",
    "remove",
    "reset",
    "destroy",
    "save",
    "create",
    "update",
    "apply",
    "reboot",
    "shutdown",
)


def _is_risky_label(text: str, *, safe_mode: bool) -> bool:
    if not safe_mode:
        return False
    t = (text or "").lower()
    return any(tok in t for tok in RISKY_SUBSTRINGS)


def _same_origin(url_a: str, url_b: str) -> bool:
    try:
        pa, pb = urlparse(url_a), urlparse(url_b)
        return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)
    except Exception:
        return False


def _normalize_url(base: str, href: str) -> Optional[str]:
    try:
        u = urljoin(base, href)
        pu = urlparse(u)
        if pu.scheme not in ("http", "https"):
            return None
        return pu._replace(fragment="").geturl()
    except Exception:
        return None


def _strip_fragment(url: str) -> str:
    try:
        return urlparse(url)._replace(fragment="").geturl()
    except Exception:
        return url


def _extensions_dict(context: RunContext) -> dict[str, Any]:
    """Normalize metadata.extensions to a dict (Pydantic may expose a non-dict Mapping)."""
    ext = context.metadata.extensions
    if ext is None:
        return {}
    if isinstance(ext, dict):
        return ext
    try:
        return dict(ext)
    except Exception:
        return {}


def _merge_cfg(plugin_config: Mapping[str, Any], context: RunContext) -> MutableMapping[str, Any]:
    out: dict[str, Any] = dict(plugin_config)
    ae = _extensions_dict(context).get("auto_explore")
    if isinstance(ae, dict):
        out = {**out, **ae}
    return out


def _active(context: RunContext, plugin_config: Mapping[str, Any]) -> bool:
    ext = _extensions_dict(context)
    if ext.get("run_mode") == "auto_explore" and isinstance(ext.get("auto_explore"), dict):
        return True
    return bool(plugin_config.get("enabled"))


def _try_login(
    page: Any,
    *,
    driver: PlaywrightPlatformDriver,
    username: str,
    password: str,
    login_strategy: str,
    hints: Mapping[str, Any],
) -> tuple[Optional[bool], str, List[str], Optional[LoginDetectionResult]]:
    """Return (login_ok, detail, errors, login_detection). ``login_ok`` None means unknown / skipped."""
    strategy = login_strategy.lower()
    if strategy == "manual_hints":
        usel = (hints.get("username_selector") or "").strip() or None
        psel = (hints.get("password_selector") or "").strip() or None
        if (usel and not psel) or (psel and not usel):
            return (
                False,
                "manual_hints: provide both username_selector and password_selector (or leave both empty to use auto_detect)",
                ["manual_hints: only one of username/password selector was set"],
                None,
            )
        if not usel and not psel:
            logger.info(
                "login_strategy manual_hints but selectors empty — using auto_detect heuristics "
                "(fill Username selector + Password selector to force CSS hints)"
            )

    ok, detail, det, errs = perform_login_with_detection(
        page,
        username=username,
        password=password,
        hints=dict(hints),
        driver=driver,
    )
    return ok, detail, errs, det


def run_auto_explore_ui(context: RunContext, plugin_config: Mapping[str, Any]) -> StepResult:
    start = time.perf_counter()
    cfg = _merge_cfg(plugin_config, context)

    if not _active(context, plugin_config):
        ext = _extensions_dict(context)
        logger.warning(
            "auto_explore_ui skipped: pipeline ran plugin but run is not an auto-explore request "
            "(run_mode=%r, auto_explore_in_extensions=%s, plugins.auto_explore_ui.enabled=%s). "
            "Use POST /run with run_mode=auto_explore and auto_explore payload, or the UI Auto explore form.",
            ext.get("run_mode"),
            isinstance(ext.get("auto_explore"), dict),
            plugin_config.get("enabled"),
        )
        summary = AutoExploreSummary(status="skipped", failed=False)
        context.merge_metadata({"executor": {"auto_explore_ui": summary}})
        return StepResult(
            layer="auto_explore_ui",
            name="auto_explore_ui",
            status=StepStatus.SKIPPED,
            detail={"reason": "disabled_or_not_requested", "summary": summary.model_dump(mode="json")},
        )

    target_url = str(cfg.get("target_url") or "").strip()
    username = str(cfg.get("username") or "")
    password = str(context.plugin_secrets.get("auto_explore_password") or "")

    login_strategy = str(cfg.get("login_strategy") or "auto_detect").lower()
    max_pages = max(1, min(int(cfg.get("max_pages") or 10), 50))
    safe_mode = bool(cfg.get("safe_mode", True))
    headless = bool(cfg.get("headless", True))
    browser = str(cfg.get("browser") or "chromium")

    hints = {
        "username_selector": cfg.get("username_selector"),
        "password_selector": cfg.get("password_selector"),
        "login_button_selector": cfg.get("login_button_selector"),
        "success_marker": cfg.get("success_marker"),
    }

    errors: List[str] = []
    warnings: List[str] = []
    skipped_risky: List[SkippedAction] = []
    visited_pages: List[PageExploreResult] = []

    if not target_url.startswith(("http://", "https://")):
        errors.append("target_url must start with http:// or https://")
    if not username:
        warnings.append("username is empty")
    if not password:
        warnings.append("password is empty — login may fail")

    if errors:
        summary = AutoExploreSummary(
            status="failed",
            failed=True,
            errors=errors,
            warnings=warnings,
            target_url=target_url,
            safe_mode=safe_mode,
            max_pages=max_pages,
            login_strategy=login_strategy,
        )
        context.merge_metadata({"executor": {"auto_explore_ui": summary}})
        return StepResult(
            layer="auto_explore_ui",
            name="auto_explore_ui",
            status=StepStatus.FAILED,
            duration_ms=(time.perf_counter() - start) * 1000,
            detail={"failure_category": ValidationCategory.UI.value, "summary": summary.model_dump(mode="json")},
            errors=errors,
        )

    driver = PlaywrightPlatformDriver(
        browser=browser,
        headless=headless,
        ignore_https_errors=True,
    )
    login_ok: Optional[bool] = None
    login_detail = ""
    login_detection: Optional[LoginDetectionResult] = None
    pages_discovered = 0

    evidence_root: Optional[Any] = None
    if isinstance(context.run_store, FileRunStore):
        evidence_root = context.run_store.runs_root / context.run_id / "auto_explore_evidence"

    shot_idx = 0

    def _screenshot(page: Any, label: str) -> List[str]:
        nonlocal shot_idx
        if evidence_root is None:
            return []
        try:
            evidence_root.mkdir(parents=True, exist_ok=True)
            shot_idx += 1
            safe = "".join(c if c.isalnum() else "-" for c in label[:24])
            path = evidence_root / f"shot-{shot_idx}-{safe}.png"
            page.screenshot(path=str(path), full_page=False)
            return [str(path)]
        except Exception as exc:
            warnings.append(f"screenshot failed: {exc}")
            return []

    try:
        logger.info(
            "auto_explore_ui starting Playwright (browser=%s headless=%s target_url=%s)",
            browser,
            headless,
            target_url,
        )
        driver.start()
        page = driver.get_page()
        logger.info("auto_explore_ui browser context ready (page created, ignore_https_errors=True)")
        console_tail: List[str] = []

        def _on_console(msg: Any) -> None:
            try:
                if msg.type in ("error", "warning"):
                    console_tail.append(f"{msg.type}:{(msg.text or '')[:400]}")
                    if len(console_tail) > 30:
                        del console_tail[:-30]
            except Exception:
                pass

        page.on("console", _on_console)

        nav = driver.navigate({"url": target_url, "wait_until": "domcontentloaded", "timeout_ms": 60_000})
        logger.info("auto_explore_ui navigate ok=%s detail=%s errors=%s", nav.ok, nav.detail, nav.errors)
        if not nav.ok:
            errors.extend(list(nav.errors))
            raise RuntimeError(
                "precheck navigation failed: "
                + (nav.errors[0] if nav.errors else "unknown")
                + (f" detail={nav.detail}" if nav.detail else "")
            )

        if POST_NAV_DEBUG_PAUSE_MS > 0:
            logger.info(
                "auto_explore_ui post-navigation debug wait (%sms, page.wait_for_timeout)",
                POST_NAV_DEBUG_PAUSE_MS,
            )
            dbg = driver.wait({"timeout_ms": float(POST_NAV_DEBUG_PAUSE_MS)})
            if not dbg.ok:
                warnings.append(f"debug wait after nav: {dbg.errors}")
            else:
                logger.info("auto_explore_ui debug wait finished detail=%s", dbg.detail)

        logger.info("auto_explore_ui running login detection + submit (strategy=%s)", login_strategy)
        login_ok, login_detail, login_errs, login_detection = _try_login(
            page,
            driver=driver,
            username=username,
            password=password,
            login_strategy=login_strategy,
            hints=hints,
        )
        errors.extend(login_errs)
        logger.info("auto_explore_ui login finished ok=%s detail=%s", login_ok, login_detail)
        if login_ok is False:
            warnings.append(f"login: {login_detail}")

        # Discover link count on post-login page (driver.read — same thread as navigate/interact)
        try:
            lr = driver.read({"evaluate": "() => document.querySelectorAll('a[href]').length"})
            if lr.ok:
                pages_discovered = int(lr.detail.get("value") or 0)
            else:
                pages_discovered = 0
        except Exception:
            pages_discovered = 0

        start_url = _strip_fragment(page.url)
        visited_norm: Set[str] = set()
        pending: deque[str] = deque()
        pending.append(start_url)
        queued: Set[str] = {start_url}

        _LINKS_SCRIPT = """() => {
          const out = [];
          for (const a of document.querySelectorAll('a[href]')) {
            const href = a.getAttribute('href') || '';
            const text = (a.innerText || a.textContent || '').trim().slice(0, 200);
            out.push({ href, text });
          }
          return out;
        }"""

        def _enqueue_links_from_current() -> None:
            try:
                dr = driver.read({"evaluate": _LINKS_SCRIPT})
                if not dr.ok:
                    warnings.append(f"link scan failed: {dr.errors}")
                    return
                items = dr.detail.get("value")
                if not isinstance(items, list):
                    warnings.append("link scan: unexpected evaluate result")
                    return
                base = page.url
                for item in items:
                    nu = _normalize_url(base, item.get("href") or "")
                    if not nu or not _same_origin(nu, base):
                        continue
                    label = item.get("text") or ""
                    if _is_risky_label(label, safe_mode=safe_mode):
                        skipped_risky.append(
                            SkippedAction(
                                kind="link",
                                reason="risky_label_safe_mode",
                                label=label,
                                href=nu,
                            )
                        )
                        continue
                    if nu not in queued:
                        queued.add(nu)
                        pending.append(nu)
            except Exception as exc:
                warnings.append(f"link scan failed: {exc}")

        while pending and len(visited_pages) < max_pages:
            url = pending.popleft()
            nu = _strip_fragment(url)
            if nu in visited_norm:
                continue
            visited_norm.add(nu)

            nres = driver.navigate({"url": nu, "wait_until": "domcontentloaded", "timeout_ms": 45_000})
            per = PageExploreResult(url=nu, title="", ok=nres.ok)
            try:
                tr = driver.read({"evaluate": "() => document.title"})
                if tr.ok and isinstance(tr.detail.get("value"), str):
                    per.title = tr.detail["value"]
                else:
                    per.title = page.title() or ""
            except Exception:
                pass

            if not nres.ok:
                per.ok = False
                per.warnings.extend(list(nres.errors))
                warnings.extend(list(nres.errors))
            else:
                per.checks.append("navigated (domcontentloaded)")
                if per.title:
                    per.checks.append("non-empty title")
                if console_tail:
                    per.warnings.append(f"recent console: {console_tail[-1]}")
                per.evidence_refs.extend(_screenshot(page, nu))

            visited_pages.append(per)
            _enqueue_links_from_current()

    except Exception as exc:
        errors.append(str(exc))
        logger.warning("auto_explore_ui exception: %s", exc)
    finally:
        try:
            logger.info("auto_explore_ui closing Playwright (browser/context/page)")
            driver.close()
        except Exception as fc:
            logger.warning("auto_explore_ui driver.close failed: %s", fc)

    failed = bool(errors) or login_ok is False
    summary = AutoExploreSummary(
        status="failed" if failed else "completed",
        failed=failed,
        browser=browser,
        headless=headless,
        safe_mode=safe_mode,
        max_pages=max_pages,
        login_strategy=login_strategy,
        target_url=target_url,
        login_ok=login_ok,
        login_detail=login_detail,
        login_detection=login_detection,
        pages_discovered=pages_discovered,
        pages_visited=len(visited_pages),
        visited=visited_pages,
        skipped_risky=skipped_risky,
        warnings=warnings,
        errors=errors,
    )
    context.merge_metadata({"executor": {"auto_explore_ui": summary}})
    duration_ms = (time.perf_counter() - start) * 1000
    detail: dict[str, Any] = {"summary": summary.model_dump(mode="json")}
    if failed:
        detail["failure_category"] = ValidationCategory.UI.value
    return StepResult(
        layer="auto_explore_ui",
        name="auto_explore_ui",
        status=StepStatus.FAILED if failed else StepStatus.SUCCEEDED,
        duration_ms=duration_ms,
        detail=detail,
        errors=errors,
    )
