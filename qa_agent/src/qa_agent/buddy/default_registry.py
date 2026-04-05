"""Wire all tools into the default ToolRegistry.

DB connections are loaded from SECRET FILES (not env vars):
  /run/secrets/db_conn_<name>   →  registered as connection "<name>"

In local dev, falls back to env vars:
  DB_CONN_<name>=<dsn>
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from qa_agent.buddy.registry import ToolRegistry
from qa_agent.buddy.tools.database import all_db_tools, register_connection
from qa_agent.buddy.tools.http_tools import all_http_tools
from qa_agent.buddy.tools.k8s import all_k8s_tools
from qa_agent.buddy.tools.k8s_config import all_k8s_config_tools
from qa_agent.buddy.tools.log_analysis import all_log_analysis_tools
from qa_agent.buddy.tools.test_generation import all_test_generation_tools

log = logging.getLogger("qa_agent.buddy.default_registry")

_SECRET_DIR = Path(os.environ.get("SECRETS_DIR", "/app/secrets"))
_ENV_PREFIX = "DB_CONN_"


def _seed_db_connections() -> None:
    """
    Load DB connection strings securely.
    Priority:
      1. Secret files at /run/secrets/db_conn_<name>  (K8s — preferred)
      2. Env vars DB_CONN_<name>                       (local dev fallback)
    DSN strings are NEVER logged.
    """
    loaded: set[str] = set()

    # 1. File-based (K8s secret volume)
    if _SECRET_DIR.is_dir():
        for f in _SECRET_DIR.glob("db_conn_*"):
            name = f.name[len("db_conn_"):]
            try:
                dsn = f.read_text(encoding="utf-8").strip()
                if dsn:
                    register_connection(name, dsn)
                    loaded.add(name)
                    log.info("db connection '%s' loaded from secret file", name)
            except OSError as e:
                log.warning("could not read db secret file %s: %s", f, e)

    # 2. Env var fallback (local dev only)
    for key, val in os.environ.items():
        if key.startswith(_ENV_PREFIX) and val:
            name = key[len(_ENV_PREFIX):].lower()
            if name not in loaded:
                register_connection(name, val)
                log.info("db connection '%s' loaded from env (local dev)", name)


def build_default_registry() -> ToolRegistry:
    _seed_db_connections()
    registry = ToolRegistry()
    registry.register_many(all_k8s_tools())
    registry.register_many(all_k8s_config_tools())
    registry.register_many(all_db_tools())
    registry.register_many(all_http_tools())
    registry.register_many(all_log_analysis_tools())
    registry.register_many(all_test_generation_tools())
    log.info("buddy registry: %d tools loaded", len(registry.all_tools()))
    return registry
