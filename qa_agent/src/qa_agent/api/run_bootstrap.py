"""Map API / UI run requests onto :class:`RunContext` and a possibly adjusted :class:`AgentConfig`."""

from __future__ import annotations

import logging
from typing import Any

from qa_agent.api.schemas import RunRequest

logger = logging.getLogger(__name__)
from qa_agent.config.application_profiles import (
    assert_resolved_target_url,
    load_application_profile_optional,
    merge_public_with_optional_profile,
    resolve_profile_yaml_path,
)
from qa_agent.config.settings import AgentConfig, PluginsConfig
from qa_agent.core.types import RunContext


def apply_run_request_to_context(body: RunRequest, agent_config: AgentConfig) -> tuple[RunContext, AgentConfig]:
    """
    Known-flow runs keep the default pipeline. Auto-explore runs force ``noop`` flow execution,
    enable ``auto_explore_ui``, disable configured ``ui_automation`` steps, and stash the password
    in :attr:`RunContext.plugin_secrets` (never in persisted metadata).
    """
    secrets: dict[str, Any] = {}
    cfg = agent_config
    meta = body.metadata

    if body.run_mode == "auto_explore" and body.auto_explore is not None:
        ae = body.auto_explore
        secrets["auto_explore_password"] = ae.password
        public = ae.model_dump(exclude={"password"}, mode="json")
        ext = dict(meta.extensions) if isinstance(meta.extensions, dict) else {}
        app_slug = (
            str(ext.get("application") or "").strip()
            or str(public.get("application") or "").strip()
        )
        profile = load_application_profile_optional(app_slug) if app_slug else None
        if profile is not None:
            public = merge_public_with_optional_profile(public, profile)
            ext["application_profile"] = profile.model_dump(mode="json")
            ext["application_profile_path"] = str(resolve_profile_yaml_path(app_slug))
            if app_slug:
                public["application"] = app_slug
        assert_resolved_target_url(public)
        ext["run_mode"] = "auto_explore"
        ext["auto_explore"] = public
        meta = meta.merged({"extensions": ext, "executor": {"flow_keys": ["noop"]}})
        pd = cfg.plugins.model_dump()
        pd["auto_explore_ui"] = {**(pd.get("auto_explore_ui") or {}), "enabled": True}
        pd["ui_automation"] = {**(pd.get("ui_automation") or {}), "enabled": False}
        cfg = cfg.model_copy(update={"plugins": PluginsConfig.model_validate(pd)})
        logger.info(
            "run_bootstrap: auto_explore target_url=%s (Playwright auto_explore_ui pipeline stage will run)",
            public.get("target_url"),
        )
    else:
        ext = dict(meta.extensions) if isinstance(meta.extensions, dict) else {}
        ext.setdefault("run_mode", "known_flow")
        # Pass known_flow credentials through extensions so config-driven flows can pick them up.
        if ext.get("kf_base_url"):
            ext["base_url"] = str(ext["kf_base_url"])
        if ext.get("kf_username"):
            ext["username"] = str(ext["kf_username"])
        if ext.get("kf_application"):
            ext.setdefault("application", str(ext["kf_application"]))
        # kf_password is kept in secrets (not in persisted metadata)
        if ext.get("kf_password"):
            secrets["password"] = str(ext.pop("kf_password"))
        # Also load application profile base_url when kf_application is set and base_url is missing
        app_slug = str(ext.get("kf_application") or ext.get("application") or "").strip()
        if app_slug and not ext.get("base_url"):
            try:
                profile = load_application_profile_optional(app_slug)
                if profile and profile.base_url:
                    ext["base_url"] = profile.base_url
                    logger.info(
                        "run_bootstrap: known_flow loaded base_url=%s from application profile %s",
                        profile.base_url,
                        app_slug,
                    )
            except Exception as exc:
                logger.debug("run_bootstrap: could not load application profile %s: %s", app_slug, exc)
        meta = meta.merged({"extensions": ext})

    return RunContext(metadata=meta, plugin_secrets=secrets), cfg
