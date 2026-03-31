"""Generic URL login + same-origin safe link exploration (Playwright)."""

from __future__ import annotations

import logging
import time
from typing import Any, List, Mapping, MutableMapping, Optional
from urllib.parse import urlparse

from qa_agent.core.types import RunContext, StepResult, StepStatus
from qa_agent.platform.auto_explore_models import (
    AutoExploreSummary,
    FeatureExploreResult,
    LoginDetectionResult,
    PageExploreResult,
    SkippedAction,
)
from qa_agent.platform.login_detection import perform_login_with_detection
from qa_agent.platform.playwright_driver import PlaywrightPlatformDriver
from qa_agent.plugins.auto_explore_exploration import run_safe_app_map_exploration
from qa_agent.store.file_store import FileRunStore
from qa_agent.validation.categories import ValidationCategory

logger = logging.getLogger(__name__)

# Temporary: after first successful navigation, block so headed browsers stay open long enough to see the page.
# Set to 0 to disable. Remove or gate behind env once workflows are stable.
POST_NAV_DEBUG_PAUSE_MS = 5000

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
    ext = _extensions_dict(context)
    ae = ext.get("auto_explore")
    if isinstance(ae, dict):
        out = {**out, **ae}
    if ext.get("application"):
        out["application"] = ext.get("application")
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
    app_structure_summary = ""
    selective_feature_summary = ""
    feature_wise: List[FeatureExploreResult] = []

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

        post_login_extra_wait_ms = float(cfg.get("post_login_extra_wait_ms") or 0)
        if post_login_extra_wait_ms > 0:
            try:
                page.wait_for_timeout(post_login_extra_wait_ms)
            except Exception as exc:
                warnings.append(f"post_login_extra_wait_ms: {exc}")

        headed_pause_ms = float(cfg.get("headed_pause_ms") or 0)
        if headed_pause_ms > 0:
            try:
                page.wait_for_timeout(headed_pause_ms)
            except Exception as exc:
                warnings.append(f"headed_pause_ms: {exc}")

        explore_mode = str(cfg.get("explore_mode") or "full").strip().lower()
        if explore_mode not in ("full", "selective"):
            explore_mode = "full"
        raw_sel = cfg.get("selected_features") or []
        if isinstance(raw_sel, str):
            selected_features = [x.strip() for x in raw_sel.split(",") if x.strip()]
        else:
            selected_features = [str(x).strip() for x in raw_sel if str(x).strip()]
        navigation_mode = str(cfg.get("navigation_mode") or "href_bfs").strip().lower()
        raw_prefixes = cfg.get("route_prefixes") or []
        route_prefixes = [str(p).strip() for p in raw_prefixes if str(p).strip()]
        # Profiles may use mode href_driven + route_prefixes — treat as prefix-scoped exploration.
        if navigation_mode == "href_driven" and route_prefixes:
            navigation_mode = "prefix_filter"
        elif navigation_mode == "href_driven":
            navigation_mode = "href_bfs"
        fk_raw = cfg.get("feature_keywords") or {}
        feature_keywords: dict[str, list[str]] = {}
        if isinstance(fk_raw, dict):
            for k, vals in fk_raw.items():
                if not isinstance(vals, list):
                    continue
                feature_keywords[str(k)] = [str(v).strip() for v in vals if str(v).strip()]

        per_page_timeout_ms = int(cfg.get("per_page_timeout_ms") or 45_000)
        post_visit_settle_ms = int(cfg.get("post_visit_settle_ms") or 800)

        start_url = _strip_fragment(page.url)

        (
            visited_pages,
            unique_queued,
            _landing_url,
            _landing_title,
            app_structure_summary,
            feature_wise,
            selective_feature_summary,
        ) = run_safe_app_map_exploration(
            driver,
            page,
            start_url=start_url,
            max_pages=max_pages,
            safe_mode=safe_mode,
            per_page_timeout_ms=per_page_timeout_ms,
            post_visit_settle_ms=post_visit_settle_ms,
            screenshot_fn=_screenshot,
            warnings=warnings,
            skipped_risky=skipped_risky,
            console_tail=console_tail,
            explore_mode=explore_mode,
            selected_features=selected_features,
            navigation_mode=navigation_mode,
            route_prefixes=route_prefixes,
            feature_keywords=feature_keywords or None,
        )
        pages_discovered = unique_queued

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
    _raw_sf = cfg.get("selected_features") or []
    if isinstance(_raw_sf, str):
        selected_for_summary = [x.strip() for x in _raw_sf.split(",") if x.strip()]
    else:
        selected_for_summary = [str(x).strip() for x in _raw_sf if str(x).strip()]
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
        application=str(cfg.get("application") or "").strip(),
        application_profile_path=str(_extensions_dict(context).get("application_profile_path") or ""),
        explore_mode=str(cfg.get("explore_mode") or "full"),
        selected_features=selected_for_summary,
        navigation_mode=str(cfg.get("navigation_mode") or "href_bfs"),
        route_prefixes=[str(p).strip() for p in (cfg.get("route_prefixes") or []) if str(p).strip()],
        app_structure_summary=app_structure_summary,
        selective_feature_summary=selective_feature_summary,
        feature_wise=feature_wise,
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
