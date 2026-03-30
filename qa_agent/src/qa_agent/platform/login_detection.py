"""
Generic Playwright login control detection — heuristics only, no product-specific markup.

Public API:
- :func:`detect_login_controls` — read-only; returns structured :class:`LoginDetectionResult`.
- :func:`perform_login_with_detection` — fill + submit using the same resolution rules as detection.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Mapping, Optional, Tuple

from qa_agent.platform.auto_explore_models import ControlPick, LoginDetectionResult

logger = logging.getLogger(__name__)

# Matches scope.locator(...) for label-based submit detection; must be a valid Playwright/CSS selector.
_SUBMIT_CANDIDATE_SELECTOR = 'button, input[type="button"], [role="button"]'

_LOGIN_LABEL_RE = re.compile(
    r"(login|log\s*in|sign\s*in|continue|submit|next|sign\s*on)",
    re.IGNORECASE,
)

_USER_ATTR_RE = re.compile(
    r"(user|login|email|account|identifier|userid|user-name)",
    re.IGNORECASE,
)


def _count(loc: Any) -> int:
    try:
        return int(loc.count())
    except Exception:
        return 0


def _visible_first(loc: Any) -> bool:
    if _count(loc) == 0:
        return False
    try:
        return bool(loc.is_visible(timeout=2_000))
    except Exception:
        return False


def _hint(hints: Mapping[str, Any], key: str) -> Optional[str]:
    v = hints.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _find_password_locator(page: Any, hints: Mapping[str, Any]) -> Tuple[Optional[Any], Optional[ControlPick]]:
    hp = _hint(hints, "password_selector")
    if hp:
        loc = page.locator(hp).first
        if _visible_first(loc):
            return loc, ControlPick(role="password", source="hint", selector=hp, detail="password_selector hint")
        # Hint did not match a visible control — fall back to auto heuristics.

    order: List[Tuple[str, str]] = [
        ('input[type="password"]', "first visible input[type=password]"),
        ('input[autocomplete="current-password"]', "autocomplete=current-password"),
    ]
    for sel, detail in order:
        loc = page.locator(sel).first
        if _visible_first(loc):
            d = detail
            if hp:
                d = f"{detail} (password_selector hint had no visible match)"
            return loc, ControlPick(role="password", source="auto", selector=sel, detail=d)

    for sel in ('input[name*="pass"]', 'input[id*="pass"]', 'input[name*="pwd"]'):
        loc = page.locator(sel).first
        if not _visible_first(loc):
            continue
        try:
            if (loc.get_attribute("type") or "").lower() == "password":
                d = "name/id password heuristic"
                if hp:
                    d += " (password_selector hint had no visible match)"
                return loc, ControlPick(role="password", source="auto", selector=sel, detail=d)
        except Exception:
            continue

    return None, None


def _form_scope(page: Any, pwd_loc: Any) -> Tuple[bool, Any]:
    try:
        form = page.locator("form").filter(has=pwd_loc).first
        if _count(form) > 0:
            return True, form
    except Exception:
        pass
    return False, page


def _find_username_locator(scope: Any, hints: Mapping[str, Any]) -> Tuple[Optional[Any], Optional[ControlPick]]:
    hu = _hint(hints, "username_selector")
    if hu:
        loc = scope.locator(hu).first
        if _visible_first(loc):
            return loc, ControlPick(role="username", source="hint", selector=hu, detail="username_selector hint")

    candidates: List[Tuple[str, str]] = [
        ('input[type="email"]', "type=email"),
        ('input[autocomplete="username"]', "autocomplete=username"),
        ('input[autocomplete="email"]', "autocomplete=email"),
    ]
    for sel, detail in candidates:
        loc = scope.locator(sel).first
        if _visible_first(loc):
            d = detail
            if hu:
                d = f"{detail} (username_selector hint had no visible match)"
            return loc, ControlPick(role="username", source="auto", selector=sel, detail=d)

    # Text inputs: prefer name/id/placeholder matching user/login/email
    try:
        n_text = int(scope.locator('input[type="text"]').count())
        for i in range(min(n_text, 25)):
            loc = scope.locator('input[type="text"]').nth(i)
            if not _visible_first(loc):
                continue
            blob = ""
            try:
                blob = (loc.get_attribute("name") or "") + " " + (loc.get_attribute("id") or "")
                blob += " " + (loc.get_attribute("placeholder") or "")
            except Exception:
                pass
            if _USER_ATTR_RE.search(blob):
                ud = "text input with user-like attributes"
                if hu:
                    ud += " (username_selector hint had no visible match)"
                return loc, ControlPick(
                    role="username",
                    source="auto",
                    selector='input[type="text"]',
                    locator_nth=i,
                    detail=ud,
                )
    except Exception:
        pass

    loc = scope.locator('input[type="text"]').first
    if _visible_first(loc):
        ud = "first visible text input in scope"
        if hu:
            ud += " (username_selector hint had no visible match)"
        return loc, ControlPick(role="username", source="auto", selector='input[type="text"]', detail=ud)

    return None, None


def _find_submit_locator(scope: Any, hints: Mapping[str, Any]) -> Tuple[Optional[Any], bool, Optional[ControlPick]]:
    """Return (locator or None, use_keyboard, pick)."""
    hs = _hint(hints, "login_button_selector")
    if hs:
        loc = scope.locator(hs).first
        if _visible_first(loc):
            return loc, False, ControlPick(role="submit", source="hint", selector=hs, detail="login_button_selector hint")

    for sel, detail in (
        ('button[type="submit"]', "button[type=submit]"),
        ('input[type="submit"]', "input[type=submit]"),
    ):
        loc = scope.locator(sel).first
        if _visible_first(loc):
            d = detail
            if hs:
                d = f"{detail} (login_button_selector hint had no visible match)"
            return loc, False, ControlPick(role="submit", source="auto", selector=sel, detail=d)

    try:
        buttons = scope.locator(_SUBMIT_CANDIDATE_SELECTOR)
        n = int(buttons.count())
        for i in range(min(n, 50)):
            b = buttons.nth(i)
            if not _visible_first(b):
                continue
            try:
                txt = (b.inner_text(timeout=1_000) or "").strip()
                aria = (b.get_attribute("aria-label") or "").strip()
                blob = f"{txt} {aria}"
                if _LOGIN_LABEL_RE.search(blob):
                    sd = f"label/aria matched: {blob[:80]}"
                    if hs:
                        sd += " (login_button_selector hint had no visible match)"
                    return (
                        b,
                        False,
                        ControlPick(
                            role="submit",
                            source="auto",
                            selector=_SUBMIT_CANDIDATE_SELECTOR,
                            locator_nth=i,
                            detail=sd,
                        ),
                    )
            except Exception:
                continue
    except Exception:
        pass

    return None, True, None


def detect_login_controls(page: Any, hints: Optional[Mapping[str, Any]] = None) -> LoginDetectionResult:
    """
    Resolve controls without filling — hints override per-field, then heuristics.

    Heuristic order (summary):
    **Password**: hint ``password_selector`` else first visible ``input[type=password]``, then
    ``autocomplete=current-password``, then ``name``/``id`` containing pass/pwd.
    **Username** (scoped to password's form when present): hint ``username_selector`` else
    ``type=email``, ``autocomplete`` username/email, then text inputs with user-like ``name``/``id``/``placeholder``,
    else first visible text input in scope.
    **Submit**: hint ``login_button_selector`` else ``button[type=submit]``, ``input[type=submit]``,
    then visible buttons whose text/aria-label matches login/sign in/continue/submit/next (regex),
    else keyboard Enter is indicated via ``submit_keyboard_fallback``.
    """
    hints = hints or {}
    notes: List[str] = []

    pwd_loc, pwd_pick = _find_password_locator(page, hints)
    if pwd_loc is None or pwd_pick is None:
        return LoginDetectionResult(ok=False, notes=["password: not found"])

    in_form, scope = _form_scope(page, pwd_loc)
    notes.append("password: in <form>" if in_form else "password: not inside <form>")

    user_loc, user_pick = _find_username_locator(scope, hints)
    if user_pick:
        notes.append(f"username: {user_pick.detail}")
    else:
        notes.append("username: not resolved")

    sub_loc, use_kb, sub_pick = _find_submit_locator(scope, hints)
    if sub_pick and not use_kb:
        notes.append(f"submit: {sub_pick.detail}")
    elif use_kb:
        notes.append("submit: will use keyboard Enter")
    else:
        notes.append("submit: not found; will use keyboard Enter")

    return LoginDetectionResult(
        ok=True,
        password=pwd_pick,
        username=user_pick,
        submit=sub_pick if sub_pick and not use_kb else None,
        submit_keyboard_fallback=use_kb or sub_loc is None,
        in_form=in_form,
        notes=notes,
    )


def _interact_payload(pick: ControlPick, *, action: str, **extra: Any) -> dict[str, Any]:
    pl: dict[str, Any] = {"action": action, "selector": pick.selector, **extra}
    if pick.locator_nth is not None:
        pl["nth"] = pick.locator_nth
    return pl


def perform_login_with_detection(
    page: Any,
    *,
    username: str,
    password: str,
    hints: Optional[Mapping[str, Any]] = None,
    driver: Any = None,
) -> Tuple[Optional[bool], str, LoginDetectionResult, List[str]]:
    """
    Fill and submit using the same resolution rules as :func:`detect_login_controls`.

    When ``driver`` is set (e.g. :class:`~qa_agent.platform.playwright_driver.PlaywrightPlatformDriver`),
    uses :meth:`~qa_agent.platform.playwright_driver.PlaywrightPlatformDriver.interact`,
    :meth:`~qa_agent.platform.playwright_driver.PlaywrightPlatformDriver.wait`, and
    :meth:`~qa_agent.platform.playwright_driver.PlaywrightPlatformDriver.read` instead of raw page
    calls where possible.

    Returns ``(login_ok, detail, detection_snapshot, errors)``. ``login_ok`` is ``None`` only if skipped.
    """
    hints = hints or {}
    errs: List[str] = []
    url_before = page.url
    use_driver = driver is not None
    if use_driver:
        logger.info("login: using Playwright driver for interact/wait/read after detection")

    det = detect_login_controls(page, hints)
    if not det.ok or det.password is None:
        logger.info("login: detection failed (no password field)")
        return False, "login detection failed", det, ["password not found"]

    pwd_loc, pwd_pick = _find_password_locator(page, hints)
    if pwd_loc is None or pwd_pick is None:
        return False, "password locator lost", det, ["password locator missing"]

    _, scope = _form_scope(page, pwd_loc)

    try:
        if use_driver:
            pl = _interact_payload(pwd_pick, action="fill", text=password, timeout_ms=15_000)
            dr = driver.interact(pl)
            if not dr.ok:
                errs.extend(list(dr.errors))
                raise RuntimeError(dr.errors[0] if dr.errors else "password fill failed")
        else:
            pwd_loc.fill(password, timeout=15_000)

        if username.strip():
            u_loc, u_pick = _find_username_locator(scope, hints)
            if u_loc is not None and u_pick is not None:
                if use_driver:
                    pl = _interact_payload(u_pick, action="fill", text=username, timeout_ms=15_000)
                    dr = driver.interact(pl)
                    if not dr.ok:
                        errs.extend(list(dr.errors))
                        raise RuntimeError(dr.errors[0] if dr.errors else "username fill failed")
                else:
                    u_loc.fill(username, timeout=15_000)
            else:
                errs.append("username not filled (not detected)")

        sub_loc, use_kb, sub_pick = _find_submit_locator(scope, hints)
        if use_driver:
            if sub_pick is not None and not use_kb:
                pl = _interact_payload(sub_pick, action="click", timeout_ms=25_000)
                dr = driver.interact(pl)
                if not dr.ok:
                    errs.extend(list(dr.errors))
                    raise RuntimeError(dr.errors[0] if dr.errors else "submit click failed")
            else:
                dr = driver.interact({"action": "press", "key": "Enter"})
                if not dr.ok:
                    errs.extend(list(dr.errors))
                    raise RuntimeError(dr.errors[0] if dr.errors else "Enter key failed")
        else:
            if sub_loc is not None and not use_kb:
                sub_loc.click(timeout=25_000)
            else:
                page.keyboard.press("Enter")

        if use_driver:
            wdr = driver.wait({"load_state": "domcontentloaded", "timeout_ms": 45_000})
            if not wdr.ok:
                errs.extend(list(wdr.errors))
                raise RuntimeError(wdr.errors[0] if wdr.errors else "wait load_state failed")
        else:
            page.wait_for_load_state("domcontentloaded", timeout=45_000)

        sm = _hint(hints, "success_marker")
        if sm:
            if use_driver:
                wdr = driver.wait({"selector": str(sm), "state": "visible", "timeout_ms": 15_000})
                snap = detect_login_controls(page, hints)
                if not wdr.ok:
                    errs.extend(list(wdr.errors))
                    return (
                        False,
                        f"success_marker not found: {wdr.errors}",
                        snap,
                        errs + list(wdr.errors),
                    )
                return True, "success_marker visible after submit", snap, errs
            try:
                page.wait_for_selector(str(sm), timeout=15_000)
                snap = detect_login_controls(page, hints)
                return True, "success_marker visible after submit", snap, errs
            except Exception as exc:
                snap = detect_login_controls(page, hints)
                return False, f"success_marker not found: {exc}", snap, errs + [str(exc)]

        if use_driver:
            rd = driver.read(
                {"evaluate": '() => document.querySelectorAll(\'input[type="password"]\').length'}
            )
            still_pwd = int(rd.detail.get("value", 99)) if rd.ok else 99
        else:
            still_pwd = page.locator('input[type="password"]').count()
        ok = still_pwd == 0 or page.url != url_before
        snap = detect_login_controls(page, hints)
        logger.info("login: post-submit heuristic ok=%s still_pwd=%s url_changed=%s", ok, still_pwd, page.url != url_before)
        return ok, "heuristic: password fields cleared or URL changed", snap, errs
    except Exception as exc:
        errs.append(str(exc))
        snap = detect_login_controls(page, hints)
        logger.warning("login: error during submit/heuristics: %s", exc)
        return False, f"login error: {exc}", snap, errs
