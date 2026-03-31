"""Load optional per-application YAML profiles from ``config/applications/*.yaml``."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LoginSection(BaseModel):
    """Login hints for the generic Playwright login flow."""

    strategy: str = "auto_detect"
    success_marker: Optional[str] = None


class NavigationSection(BaseModel):
    """
    How to scope navigation during exploration.

    - ``href_bfs``: discover all same-origin links (default).
    - ``prefix_filter``: only enqueue links whose path starts with one of ``route_prefixes``.
    """

    mode: str = "href_bfs"
    route_prefixes: List[str] = Field(default_factory=list)


class ApplicationProfile(BaseModel):
    """
    Application-agnostic profile. Keys are generic; values are filled per deployment.

    ``feature_keywords`` maps a user-facing feature label to URL/path keyword synonyms
    used for selective exploration (href + label matching).
    """

    application: str
    base_url: str = ""
    login: LoginSection = Field(default_factory=LoginSection)
    navigation: NavigationSection = Field(default_factory=NavigationSection)
    safe_mode: bool = True
    feature_keywords: Dict[str, List[str]] = Field(default_factory=dict)


def _config_dir() -> Path:
    env = os.environ.get("QA_AGENT_CONFIG_PATH")
    if env:
        return Path(env).expanduser().resolve().parent
    here = Path(__file__).resolve()
    # qa_agent/src/qa_agent/config/application_profiles.py -> parents[3] == qa_agent project root
    return here.parents[3] / "config"


def applications_directory() -> Path:
    """Directory containing ``<application>.yaml`` profiles (next to ``default.yaml``)."""
    return _config_dir() / "applications"


def _slug_path(application: str) -> Path:
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in application.strip().lower())
    slug = slug.strip("-") or "app"
    return applications_directory() / f"{slug}.yaml"


def resolve_profile_yaml_path(application: str) -> Path:
    """Resolved filesystem path for ``config/applications/<slug>.yaml``."""
    return _slug_path(application)


def load_application_profile(application: str) -> ApplicationProfile:
    """Load YAML from ``config/applications/<application>.yaml``."""
    path = _slug_path(application)
    if not path.is_file():
        raise FileNotFoundError(f"Application profile not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = dict(raw)
    if "application" not in data and application:
        data["application"] = application.strip()
    return ApplicationProfile.model_validate(data)


def load_application_profile_optional(application: str) -> Optional[ApplicationProfile]:
    if not (application or "").strip():
        return None
    try:
        prof = load_application_profile(application)
        logger.info("application_profile: loaded application=%s path=%s", prof.application, _slug_path(application))
        return prof
    except FileNotFoundError:
        logger.warning(
            "application_profile: no file for application=%r under %s",
            application,
            applications_directory(),
        )
        return None
    except Exception as exc:
        logger.warning("application_profile: failed to load application=%r: %s", application, exc)
        raise


def profile_to_auto_explore_defaults(profile: ApplicationProfile) -> Dict[str, Any]:
    """Flat map merged with request fields (request non-empty values win)."""
    return {
        "target_url": profile.base_url.strip(),
        "login_strategy": profile.login.strategy,
        "success_marker": profile.login.success_marker,
        "safe_mode": profile.safe_mode,
        "navigation_mode": profile.navigation.mode,
        "route_prefixes": list(profile.navigation.route_prefixes),
        "feature_keywords": {k: list(v) for k, v in profile.feature_keywords.items()},
    }


def merge_application_profile_into_auto_explore(
    public_payload: Dict[str, Any],
    profile: ApplicationProfile,
) -> Dict[str, Any]:
    """
    Overlay request on profile defaults: explicit non-empty request values override.

    ``profile`` supplies ``target_url`` when the request ``target_url`` is empty.
    """
    defaults = profile_to_auto_explore_defaults(profile)
    out = {**defaults, **public_payload}
    req_url = str(public_payload.get("target_url") or "").strip()
    if not req_url:
        out["target_url"] = str(defaults["target_url"] or "").strip()
    if not str(public_payload.get("success_marker") or "").strip() and defaults.get("success_marker"):
        out["success_marker"] = defaults.get("success_marker")
    return out


def merge_public_with_optional_profile(
    public_payload: Dict[str, Any],
    profile: Optional[ApplicationProfile],
) -> Dict[str, Any]:
    if profile is None:
        return dict(public_payload)
    return merge_application_profile_into_auto_explore(public_payload, profile)


def assert_resolved_target_url(payload: Dict[str, Any]) -> None:
    """Raise if ``target_url`` is still missing after profile merge."""
    if not str(payload.get("target_url") or "").strip():
        raise ValueError(
            "auto_explore requires target_url or an application profile with base_url "
            "(config/applications/<application>.yaml)"
        )
