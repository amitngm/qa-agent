"""Structured results for generic UI auto-exploration (separate from configured flow UI automation)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ControlPick(BaseModel):
    """One resolved login control for metadata (Playwright locator string + provenance)."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["username", "password", "submit"]
    source: Literal["hint", "auto"]
    selector: str = Field(description="Primary selector string used with page.locator(...).")
    detail: str = ""
    locator_nth: Optional[int] = Field(
        default=None,
        description="When set, target scope.locator(selector).nth(locator_nth) (disambiguation).",
    )


class LoginDetectionResult(BaseModel):
    """Structured output of generic login control detection (no secrets)."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = False
    password: Optional[ControlPick] = None
    username: Optional[ControlPick] = None
    submit: Optional[ControlPick] = None
    submit_keyboard_fallback: bool = False
    in_form: bool = False
    notes: List[str] = Field(default_factory=list)


class SkippedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(description="link | button | other")
    reason: str = ""
    label: Optional[str] = None
    href: Optional[str] = None


class PageExploreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    ok: bool = True
    checks: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    skipped_actions: List[SkippedAction] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    heading: str = ""
    forms_count: int = 0
    tables_count: int = 0
    buttons_count: int = 0
    console_errors: List[str] = Field(default_factory=list)
    network_failures: List[str] = Field(default_factory=list)
    discovery_buckets: List[str] = Field(default_factory=list)
    matched_features: List[str] = Field(default_factory=list)


class FeatureExploreResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    visited_urls: List[str] = Field(default_factory=list)


class AutoExploreSummary(BaseModel):
    """Stored under ``executor.auto_explore_ui`` on run metadata (no secrets)."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="completed | skipped | failed")
    driver: str = "playwright"
    browser: str = "chromium"
    headless: bool = True
    safe_mode: bool = True
    max_pages: int = 10
    login_strategy: str = ""
    target_url: str = ""
    login_ok: Optional[bool] = None
    login_detail: str = ""
    login_detection: Optional[LoginDetectionResult] = Field(
        default=None,
        description="Structured result from generic login control detection (no secrets).",
    )
    pages_discovered: int = 0
    pages_visited: int = 0
    visited: List[PageExploreResult] = Field(default_factory=list)
    skipped_risky: List[SkippedAction] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    failed: bool = False
    application: str = ""
    application_profile_path: str = ""
    explore_mode: str = "full"
    selected_features: List[str] = Field(default_factory=list)
    navigation_mode: str = "href_bfs"
    route_prefixes: List[str] = Field(default_factory=list)
    app_structure_summary: str = ""
    selective_feature_summary: str = ""
    feature_wise: List[FeatureExploreResult] = Field(default_factory=list)
