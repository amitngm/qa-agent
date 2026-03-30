"""
Generic same-origin safe BFS app exploration after login.

Collects navigation candidates from semantic regions (nav, aside, roles, main, all links),
queues internal URLs with a safe-label policy, visits pages, and records inventory + evidence.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from qa_agent.platform.auto_explore_models import (
    FeatureExploreResult,
    PageExploreResult,
    SkippedAction,
)
from qa_agent.platform.playwright_driver import PlaywrightPlatformDriver

logger = logging.getLogger(__name__)

# Align with auto_explore_ui.safe_mode policy (substring match on label text).
RISKY_SUBSTRINGS = (
    "delete",
    "remove",
    "terminate",
    "reset",
    "destroy",
    "save",
    "submit",
    "create",
    "update",
    "apply",
    "reboot",
    "shutdown",
    "approve",
    "reject",
    "disable",
    "enable",
)


def is_risky_label(text: str, *, safe_mode: bool) -> bool:
    if not safe_mode:
        return False
    t = (text or "").lower()
    return any(tok in t for tok in RISKY_SUBSTRINGS)


# Destructive-looking routes (navigation by href is otherwise allowed).
RISKY_PATH_SUBSTRINGS = (
    "/delete",
    "/destroy",
    "/remove",
    "/terminate",
    "/shutdown",
    "/reboot",
    "action=delete",
)

# Generic session / sign-out paths to avoid during automated exploration.
SKIP_NAV_PATH_SUBSTRINGS = (
    "/logout",
    "/log-out",
    "/signout",
    "/sign-out",
    "/sign_out",
    "/session/logout",
)


def is_risky_path(path: str) -> bool:
    pl = (path or "").lower()
    return any(s in pl for s in RISKY_PATH_SUBSTRINGS)


def is_skipped_navigation_path(path: str) -> bool:
    pl = (path or "").lower()
    if not pl:
        return False
    return any(s in pl for s in SKIP_NAV_PATH_SUBSTRINGS)


def _match_blob_for_features(label: str, href: str) -> str:
    """Lowercase blob: visible text + path words + full URL for substring matching."""
    p = (urlparse(href).path or "").lower()
    path_words = re.sub(r"[/\-_]+", " ", p)
    lab = (label or "").lower()
    return f"{lab} {path_words} {href.lower()}"


def _feature_string_matches(label: str, href: str, feature: str) -> bool:
    """
    Match a user feature to link label and/or href path keywords (generic).

    Multi-word features require every word to appear as a substring in the blob.
    Single short tokens (e.g. VM, VPC) match path/slug text (e.g. virtual-machines, vpcs).
    """
    ft = feature.strip()
    if not ft:
        return False
    blob = _match_blob_for_features(label, href)
    words = [w for w in re.split(r"[\s,]+", ft) if w]
    if not words:
        return False
    for w in words:
        wl = w.lower()
        if len(wl) < 2:
            continue
        if wl not in blob:
            return False
    return True


def matching_feature_tokens(label: str, href: str, selected: List[str]) -> List[str]:
    """Which selected feature strings match this link (text and/or path keywords)."""
    if not selected:
        return []
    out: List[str] = []
    for raw in selected:
        tok = raw.strip()
        if not tok:
            continue
        if _feature_string_matches(label, href, tok):
            out.append(tok)
    return out


def _same_origin(url_a: str, url_b: str) -> bool:
    from urllib.parse import urlparse

    try:
        pa, pb = urlparse(url_a), urlparse(url_b)
        return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)
    except Exception:
        return False


def _normalize_url(base: str, href: str) -> Optional[str]:
    from urllib.parse import urlparse, urljoin

    try:
        u = urljoin(base, href)
        pu = urlparse(u)
        if pu.scheme not in ("http", "https"):
            return None
        return pu._replace(fragment="").geturl()
    except Exception:
        return None


def _strip_fragment(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url)._replace(fragment="").geturl()
    except Exception:
        return url


# Href-first discovery: visible A[href] in landmark regions, then global fallback.
# Dedupe by href string (first bucket wins). Labels: text → aria-label → title → data-testid → href.
DISCOVERY_SCRIPT = """() => {
  function trim(s, n) { return (s || '').trim().slice(0, n || 200); }
  function visible(el) {
    try {
      const r = el.getBoundingClientRect();
      const st = window.getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
      return r.width > 0 && r.height > 0;
    } catch (e) { return false; }
  }
  function linkLabel(a) {
    const t = trim(a.innerText || a.textContent || '', 200);
    if (t) return t;
    let al = trim(a.getAttribute('aria-label') || '', 200);
    if (al) return al;
    try {
      const subAl = a.querySelector('[aria-label]');
      if (subAl) {
        al = trim(subAl.getAttribute('aria-label') || '', 200);
        if (al) return al;
      }
    } catch (e) {}
    let ti = trim(a.getAttribute('title') || '', 200);
    if (ti) return ti;
    try {
      const subT = a.querySelector('[title]');
      if (subT) {
        ti = trim(subT.getAttribute('title') || '', 200);
        if (ti) return ti;
      }
    } catch (e) {}
    const dt = trim(a.getAttribute('data-testid') || a.getAttribute('data-test') || '', 120);
    if (dt) return dt;
    return trim(a.getAttribute('href') || '', 200);
  }
  const FAMILIES = [
    ['nav_a', 'nav a[href], [role="navigation"] a[href]'],
    ['aside_a', 'aside a[href]'],
    ['header_a', 'header a[href]'],
    ['main_a', 'main a[href]'],
    ['any_a', 'a[href]'],
  ];
  const globalSeenHref = new Set();
  const countsByFamily = {};
  const candidates = [];
  for (const pair of FAMILIES) {
    const fname = pair[0];
    const sel = pair[1];
    let n = 0;
    try {
      document.querySelectorAll(sel).forEach(a => {
        if (!a || a.tagName !== 'A') return;
        if (!visible(a)) return;
        const href = a.getAttribute('href');
        if (!href || href.startsWith('javascript:') || href.trim() === '' || href === '#') return;
        const hrefKey = href.trim();
        if (globalSeenHref.has(hrefKey)) return;
        globalSeenHref.add(hrefKey);
        n++;
        const label = linkLabel(a);
        candidates.push({
          href: hrefKey,
          text: label,
          bucket: fname,
          family: fname,
          innerText: trim(a.innerText || '', 200),
          ariaLabel: trim(a.getAttribute('aria-label') || '', 200),
          title: trim(a.getAttribute('title') || '', 200),
          dataTestid: trim(a.getAttribute('data-testid') || '', 120),
          tagName: 'a',
          resolvable: true,
        });
      });
    } catch (e) {}
    countsByFamily[fname] = n;
  }
  return { countsByFamily, candidates, unresolvedCount: 0 };
}"""

PAGE_METRICS_SCRIPT = """() => {
  const h1 = document.querySelector('h1');
  const h2 = document.querySelector('h2');
  let heading = '';
  if (h1 && (h1.innerText || '').trim()) heading = (h1.innerText || '').trim().slice(0, 500);
  else if (h2 && (h2.innerText || '').trim()) heading = (h2.innerText || '').trim().slice(0, 500);
  return {
    forms: document.querySelectorAll('form').length,
    tables: document.querySelectorAll('table').length,
    buttons: document.querySelectorAll('button').length,
    heading: heading
  };
}"""


NAV_PROBE_SCRIPT = """() => {
  const q = (s) => { try { return document.querySelectorAll(s).length; } catch (e) { return 0; } };
  return {
    navA: q('nav a[href]') + q('[role="navigation"] a[href]'),
    asideA: q('aside a[href]'),
    headerA: q('header a[href]'),
    mainA: q('main a[href]'),
    allAnchors: q('a[href]'),
  };
}"""

# Generic menu / sidebar toggle (no app-specific IDs). Click at most one control.
EXPAND_NAV_SCRIPT = """() => {
  const selectors = [
    'button[aria-label*="menu" i]',
    '[role="button"][aria-label*="menu" i]',
    'button[aria-label*="Menu"]',
    '[aria-label*="sidebar" i]',
    '[aria-label*="navigation" i][role="button"]',
    'header button[aria-expanded="false"]',
    '[role="banner"] button[aria-label]',
  ];
  for (const sel of selectors) {
    try {
      const el = document.querySelector(sel);
      if (!el) continue;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      const al = (el.getAttribute('aria-label') || '').slice(0, 160);
      el.click();
      return { attempted: true, selector: sel, ariaLabel: al };
    } catch (e) {}
  }
  return { attempted: false };
}"""


def _label_from_discovery_item(item: Dict[str, Any]) -> str:
    for k in ("text", "innerText", "ariaLabel", "title", "dataTestid"):
        v = item.get(k)
        if v and str(v).strip():
            return str(v).strip()[:200]
    href = str(item.get("href") or "").strip()
    if href:
        try:
            return (urlparse(href).path or href)[:200]
        except Exception:
            return href[:200]
    return ""


def _log_discovery_payload(
    counts: Dict[str, Any],
    candidates: List[dict[str, Any]],
    unresolved: int,
) -> None:
    logger.info("exploration: discovery counts_by_family=%s", counts)
    logger.info("exploration: discovery candidates_total=%s unresolved_no_href=%s", len(candidates), unresolved)
    sample: List[str] = []
    for c in candidates[:20]:
        lab = _label_from_discovery_item(c)
        fam = c.get("family") or c.get("bucket") or "?"
        href = (c.get("href") or "")[:120]
        sample.append(f"[{fam}] label={lab!r} href={href!r} role={c.get('role')!r}")
    if sample:
        logger.info("exploration: discovery sample (up to 20):\n  %s", "\n  ".join(sample))


def _pre_discovery_spa_wait(
    page: Any,
    driver: PlaywrightPlatformDriver,
    warnings: List[str],
) -> None:
    """Wait/retry so SPA nav/sidebars can hydrate before the first discovery pass."""
    wait_ms = [600, 1800, 4000]
    for i, ms in enumerate(wait_ms):
        try:
            page.wait_for_timeout(float(ms))
        except Exception as exc:
            warnings.append(f"pre_discovery_wait_ms:{exc}")
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        try:
            pr = driver.read({"evaluate": NAV_PROBE_SCRIPT})
            if pr.ok and isinstance(pr.detail.get("value"), dict):
                v = pr.detail["value"]
                tot = sum(int(v.get(k) or 0) for k in v)
                anchors = int(v.get("allAnchors") or 0)
                logger.info(
                    "exploration: pre-discovery wait pass %s/%s aggregate_signals=%s anchors=%s detail=%s",
                    i + 1,
                    len(wait_ms),
                    tot,
                    anchors,
                    v,
                )
                if anchors >= 1 or tot >= 6:
                    return
        except Exception as exc:
            logger.debug("exploration: pre-discovery probe failed: %s", exc)


def _maybe_expand_navigation_rail(
    page: Any,
    driver: PlaywrightPlatformDriver,
    warnings: List[str],
) -> bool:
    """Try one generic menu/sidebar toggle click (best-effort)."""
    try:
        r = driver.read({"evaluate": EXPAND_NAV_SCRIPT})
        if not r.ok:
            warnings.append(f"expand_nav_script:{r.errors}")
            logger.info("exploration: sidebar/menu expand — script failed: %s", r.errors)
            return False
        val = r.detail.get("value")
        if isinstance(val, dict) and val.get("attempted"):
            logger.info(
                "exploration: sidebar/menu expand attempted selector=%s ariaLabel=%r",
                val.get("selector"),
                (val.get("ariaLabel") or "")[:80],
            )
            try:
                page.wait_for_timeout(900.0)
            except Exception as exc:
                warnings.append(f"post_expand_wait:{exc}")
            return True
        logger.info("exploration: sidebar/menu expand — no matching toggle found (skipped)")
        return False
    except Exception as exc:
        warnings.append(f"expand_nav:{exc}")
        logger.warning("exploration: expand navigation rail failed: %s", exc)
        return False


def _build_feature_wise_results(
    visited: List[PageExploreResult], selected: List[str]
) -> List[FeatureExploreResult]:
    by_f: Dict[str, List[str]] = {f: [] for f in selected}
    for p in visited:
        for mf in p.matched_features:
            if mf in by_f and p.url not in by_f[mf]:
                by_f[mf].append(p.url)
    return [FeatureExploreResult(feature=k, visited_urls=v) for k, v in by_f.items()]


def _build_selective_summary(feature_wise: List[FeatureExploreResult]) -> str:
    lines = ["=== Selective feature exploration ==="]
    for fr in feature_wise:
        lines.append(f"Feature {fr.feature!r}: {len(fr.visited_urls)} page(s)")
        for u in fr.visited_urls:
            lines.append(f"  - {u}")
    return "\n".join(lines)


def _build_app_structure_summary(
    landing_url: str,
    landing_title: str,
    visited: List[PageExploreResult],
    unique_queued: int,
) -> str:
    lines = [
        "=== App exploration summary (generic) ===",
        f"Landing: {landing_url}",
        f"Landing title: {landing_title}",
        f"Unique URLs queued (safe, same-origin): {unique_queued}",
        f"Pages visited (recorded): {len(visited)}",
        "",
    ]
    for i, p in enumerate(visited, 1):
        lines.append(
            f"{i}. {p.url}\n"
            f"   title={p.title!r} heading={p.heading!r}\n"
            f"   forms={p.forms_count} tables={p.tables_count} buttons={p.buttons_count}\n"
            f"   buckets={p.discovery_buckets}\n"
            f"   console_errors={len(p.console_errors)} network_failures={len(p.network_failures)}"
        )
    return "\n".join(lines)


def run_safe_app_map_exploration(
    driver: PlaywrightPlatformDriver,
    page: Any,
    *,
    start_url: str,
    max_pages: int,
    safe_mode: bool,
    per_page_timeout_ms: int,
    post_visit_settle_ms: int,
    screenshot_fn: Callable[[Any, str], List[str]],
    warnings: List[str],
    skipped_risky: List[SkippedAction],
    console_tail: List[str],
    explore_mode: str = "full",
    selected_features: Optional[List[str]] = None,
) -> Tuple[
    List[PageExploreResult],
    int,
    str,
    str,
    str,
    List[FeatureExploreResult],
    str,
]:
    """
    BFS over same-origin URLs discovered from generic nav/link scans.

    ``selective`` mode only enqueues URLs whose link label or href matches a feature token
    (case-insensitive substring). Landing is always visited first as context.

    Returns:
        visited_pages, unique_urls_queued, landing_url, landing_title, app_structure_summary,
        feature_wise, selective_feature_summary
    """
    selected_features = list(selected_features or [])
    selective = explore_mode == "selective" and bool(selected_features)

    landing_url = _strip_fragment(start_url)
    landing_title = ""
    try:
        landing_title = page.title() or ""
    except Exception as exc:
        logger.warning("exploration: could not read landing title: %s", exc)

    visit_console: List[str] = []
    visit_network: List[str] = []

    def reset_buffers() -> None:
        visit_console.clear()
        visit_network.clear()

    def on_console(msg: Any) -> None:
        try:
            if msg.type in ("error", "warning"):
                text = (msg.text or "")[:500]
                visit_console.append(f"{msg.type}:{text}")
                if len(visit_console) > 80:
                    del visit_console[:-80]
        except Exception as exc:
            logger.debug("exploration console handler: %s", exc)

    def on_request_failed(req: Any) -> None:
        try:
            fail = getattr(req, "failure", None)
            fail_s = str(fail) if fail is not None else ""
            visit_network.append(f"requestfailed {req.url} {fail_s}")
            if len(visit_network) > 120:
                del visit_network[:-120]
        except Exception as exc:
            logger.debug("exploration requestfailed handler: %s", exc)

    def on_response(resp: Any) -> None:
        try:
            status = resp.status
            if status >= 400:
                visit_network.append(f"http_{status} {resp.url}")
        except Exception:
            pass

    try:
        page.on("console", on_console)
        page.on("requestfailed", on_request_failed)
        page.on("response", on_response)
    except Exception as exc:
        warnings.append(f"exploration: could not attach page listeners: {exc}")

    pending: Deque[str] = deque()
    url_buckets: Dict[str, Set[str]] = {}
    queued_urls: Set[str] = set()
    url_feature_tags: Dict[str, Set[str]] = {}
    if selective:
        url_feature_tags[landing_url] = set(selected_features)
        logger.info(
            "exploration: selective mode — features=%s (case-insensitive substring match)",
            selected_features,
        )

    def enqueue(nu: str, bucket: str) -> None:
        if nu in url_buckets:
            url_buckets[nu].add(bucket)
        else:
            url_buckets[nu] = {bucket}
        if nu not in queued_urls:
            queued_urls.add(nu)
            pending.append(nu)

    enqueue(landing_url, "landing")

    visited_pages: List[PageExploreResult] = []
    visited_norm: Set[str] = set()
    bfs_count = 0

    logger.info(
        "exploration: starting BFS explore_mode=%s (max_pages=%s safe_mode=%s landing=%s)",
        explore_mode,
        max_pages,
        safe_mode,
        landing_url,
    )

    while pending and bfs_count < max_pages:
        raw_url = pending.popleft()
        nu = _strip_fragment(raw_url)
        if nu in visited_norm:
            continue
        visited_norm.add(nu)
        bfs_count += 1

        buckets_for_url = sorted(url_buckets.get(nu, {"unknown"}))
        reset_buffers()

        logger.info(
            "exploration: [%s/%s] navigating url=%s buckets=%s",
            bfs_count,
            max_pages,
            nu,
            buckets_for_url,
        )

        try:
            nres = driver.navigate(
                {
                    "url": nu,
                    "wait_until": "domcontentloaded",
                    "timeout_ms": per_page_timeout_ms,
                }
            )
        except Exception as exc:
            logger.warning("exploration: navigate exception url=%s: %s", nu, exc)
            warnings.append(f"exploration navigate {nu}: {exc}")
            mf_fail = sorted(url_feature_tags.get(nu, set())) if selective else []
            visited_pages.append(
                PageExploreResult(
                    url=nu,
                    title="",
                    ok=False,
                    checks=["navigate_exception"],
                    discovery_buckets=buckets_for_url,
                    warnings=[str(exc)],
                    matched_features=mf_fail,
                )
            )
            continue

        if not nres.ok:
            logger.warning("exploration: navigate failed url=%s errors=%s", nu, nres.errors)
            mf_fail = sorted(url_feature_tags.get(nu, set())) if selective else []
            visited_pages.append(
                PageExploreResult(
                    url=nu,
                    title="",
                    ok=False,
                    checks=["navigation_failed"],
                    warnings=list(nres.errors),
                    discovery_buckets=buckets_for_url,
                    matched_features=mf_fail,
                )
            )
            warnings.extend([f"bfs:{nu}: {e}" for e in nres.errors])
            continue

        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(30_000, per_page_timeout_ms))
        except Exception as exc:
            logger.warning("exploration: domcontentloaded wait url=%s: %s", nu, exc)

        try:
            page.wait_for_timeout(float(post_visit_settle_ms))
        except Exception as exc:
            logger.warning("exploration: post-visit settle url=%s: %s", nu, exc)

        if bfs_count == 1:
            logger.info(
                "exploration: first-page SPA pre-discovery (staggered wait/retry + optional menu toggle)"
            )
            _pre_discovery_spa_wait(page, driver, warnings)
            expand_attempted = _maybe_expand_navigation_rail(page, driver, warnings)
            logger.info(
                "exploration: first-page menu/sidebar expand attempted=%s",
                expand_attempted,
            )
            try:
                page.wait_for_timeout(500.0)
            except Exception as exc:
                warnings.append(f"post_spa_settle:{exc}")

        title = ""
        try:
            title = page.title() or ""
        except Exception as exc:
            logger.warning("exploration: title read failed: %s", exc)

        heading = ""
        forms_count = tables_count = buttons_count = 0
        try:
            mr = driver.read({"evaluate": PAGE_METRICS_SCRIPT})
            if mr.ok and isinstance(mr.detail.get("value"), dict):
                v = mr.detail["value"]
                forms_count = int(v.get("forms") or 0)
                tables_count = int(v.get("tables") or 0)
                buttons_count = int(v.get("buttons") or 0)
                heading = str(v.get("heading") or "")
        except Exception as exc:
            logger.warning("exploration: metrics evaluate failed url=%s: %s", nu, exc)
            warnings.append(f"metrics:{nu}:{exc}")

        ce = list(visit_console)
        nf = list(visit_network)
        console_tail.clear()
        console_tail.extend(ce[-30:])

        if selective:
            matched_features = sorted(url_feature_tags.get(nu, set()))
        else:
            matched_features = []

        evidence = []
        try:
            evidence = screenshot_fn(page, f"bfs-{bfs_count}-{nu[:48]}")
        except Exception as exc:
            logger.warning("exploration: screenshot failed: %s", exc)
            warnings.append(f"screenshot:{nu}:{exc}")

        per = PageExploreResult(
            url=nu,
            title=title,
            ok=True,
            checks=["navigated", "domcontentloaded", "metrics"],
            heading=heading,
            forms_count=forms_count,
            tables_count=tables_count,
            buttons_count=buttons_count,
            console_errors=ce,
            network_failures=nf,
            discovery_buckets=buckets_for_url,
            evidence_refs=evidence,
            matched_features=matched_features,
        )
        visited_pages.append(per)
        logger.info(
            "exploration: recorded url=%s title=%r heading=%r forms=%s tables=%s buttons=%s "
            "console_lines=%s network_lines=%s matched_features=%s",
            nu,
            title[:80] if title else "",
            heading[:80] if heading else "",
            forms_count,
            tables_count,
            buttons_count,
            len(ce),
            len(nf),
            matched_features,
        )

        # Discover outgoing same-origin links from current page.
        try:
            base = page.url
            dr = driver.read({"evaluate": DISCOVERY_SCRIPT})
            if not dr.ok:
                warnings.append(f"discovery read failed: {dr.errors}")
                continue
            payload = dr.detail.get("value")
            raw_items: List[dict[str, Any]]
            if isinstance(payload, dict):
                raw_items = list(payload.get("candidates") or [])
                counts_by_family = payload.get("countsByFamily") or {}
                unresolved_ct = int(payload.get("unresolvedCount") or 0)
                _log_discovery_payload(counts_by_family, raw_items, unresolved_ct)
            elif isinstance(payload, list):
                raw_items = list(payload)
                counts_by_family = {}
                unresolved_ct = 0
            else:
                warnings.append("discovery: unexpected value")
                continue

            logger.info(
                "exploration: navigation discovery started — resolvable_candidates=%s url=%s",
                len([x for x in raw_items if isinstance(x, dict) and (x.get("href") or "").strip()]),
                nu,
            )

            discovered = 0
            skipped_selective = 0
            skipped_external = 0
            routes_new_this_page: List[str] = []
            for item in raw_items:
                try:
                    if not isinstance(item, dict):
                        continue
                    href = str(item.get("href") or "").strip()
                    if not href or href.startswith("javascript:") or href == "#":
                        continue
                    label = _label_from_discovery_item(item) or str(item.get("text") or "")
                    bucket = str(item.get("bucket") or item.get("family") or "unknown")
                    nu2 = _normalize_url(base, href)
                    if not nu2:
                        continue
                    if not _same_origin(nu2, base):
                        skipped_external += 1
                        logger.info(
                            "exploration: skip non-same-origin link label=%r href=%s",
                            (label[:80] + "…") if len(label) > 80 else label,
                            nu2,
                        )
                        skipped_risky.append(
                            SkippedAction(
                                kind="link",
                                reason="external_or_cross_origin",
                                label=label[:200] if label else "(empty)",
                                href=nu2,
                            )
                        )
                        continue
                    nu2 = _strip_fragment(nu2)
                    pathname = urlparse(nu2).path or ""
                    if is_skipped_navigation_path(pathname):
                        skipped_risky.append(
                            SkippedAction(
                                kind="link",
                                reason="navigation_skip_logout",
                                label=label[:200] if label else "(empty)",
                                href=nu2,
                            )
                        )
                        logger.info(
                            "exploration: skip sensitive nav path %s label=%r",
                            pathname,
                            (label[:80] + "…") if len(label) > 80 else label,
                        )
                        continue
                    if safe_mode and is_risky_path(pathname):
                        skipped_risky.append(
                            SkippedAction(
                                kind="link",
                                reason="risky_path",
                                label=label[:200] if label else "(empty)",
                                href=nu2,
                            )
                        )
                        logger.info(
                            "exploration: skipped risky path=%s label=%r",
                            pathname,
                            (label[:80] + "…") if len(label) > 80 else label,
                        )
                        continue
                    if safe_mode and is_risky_label(label, safe_mode=True):
                        skipped_risky.append(
                            SkippedAction(
                                kind="link",
                                reason="risky_label_safe_mode",
                                label=label[:200] if label else "(empty)",
                                href=nu2,
                            )
                        )
                        logger.info(
                            "exploration: skipped risky label=%r url=%s",
                            (label[:120] + "…") if len(label) > 120 else label,
                            nu2,
                        )
                        continue
                    if selective:
                        mfs = matching_feature_tokens(label, nu2, selected_features)
                        if not mfs:
                            skipped_selective += 1
                            skipped_risky.append(
                                SkippedAction(
                                    kind="link",
                                    reason="selective_not_matched",
                                    label=label[:200] if label else "(empty)",
                                    href=nu2,
                                )
                            )
                            logger.info(
                                "exploration: selective skip (no feature match in text/path) "
                                "label=%r url=%s",
                                (label[:120] + "…") if len(label) > 120 else label,
                                nu2,
                            )
                            continue
                        if nu2 not in url_feature_tags:
                            url_feature_tags[nu2] = set()
                        url_feature_tags[nu2].update(mfs)
                        logger.info(
                            "exploration: selective match features=%s label=%r url=%s",
                            mfs,
                            (label[:120] + "…") if len(label) > 120 else label,
                            nu2,
                        )
                    if nu2 not in url_buckets:
                        url_buckets[nu2] = {bucket}
                    else:
                        url_buckets[nu2].add(bucket)
                    if nu2 not in queued_urls:
                        pending.append(nu2)
                        queued_urls.add(nu2)
                        discovered += 1
                        routes_new_this_page.append(pathname or nu2)
                except Exception as exc:
                    logger.warning("exploration: enqueue item failed: %s", exc)
                    warnings.append(f"enqueue:{exc}")

            logger.info(
                "exploration: discovery from url=%s new_unique_queued=%s skipped_selective=%s "
                "skipped_external=%s pending_size=%s",
                nu,
                discovered,
                skipped_selective,
                skipped_external,
                len(pending),
            )
            if routes_new_this_page:
                logger.info(
                    "exploration: new internal routes this page (sample up to 40)=%s",
                    routes_new_this_page[:40],
                )
            logger.info(
                "exploration: visited_routes_so_far count=%s last=%s",
                len(visited_pages),
                nu,
            )
        except Exception as exc:
            logger.warning("exploration: discovery block failed: %s", exc)
            warnings.append(f"discovery_block:{exc}")

    unique_queued = len(queued_urls)
    summary_text = _build_app_structure_summary(
        landing_url,
        landing_title,
        visited_pages,
        unique_queued,
    )
    feature_wise = (
        _build_feature_wise_results(visited_pages, selected_features)
        if selective
        else []
    )
    selective_summary = _build_selective_summary(feature_wise) if selective else ""
    logger.info(
        "exploration: BFS complete visited=%s unique_internal_routes_queued=%s explore_mode=%s",
        len(visited_pages),
        unique_queued,
        explore_mode,
    )
    logger.info(
        "exploration: all visited routes=%s",
        [p.url for p in visited_pages],
    )
    logger.info(
        "exploration: unique queued internal routes (sample up to 60)=%s",
        sorted(queued_urls)[:60],
    )
    return (
        visited_pages,
        unique_queued,
        landing_url,
        landing_title,
        summary_text,
        feature_wise,
        selective_summary,
    )
