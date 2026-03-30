## AI Buddy — Report

**Date**: 2026-03-30
**Workspace**: `AI Buddy`

### What’s in this repo

- **`qa_agent/`**: A Python package (`qa-agent`) described as a “Generic, config-driven QA agent orchestration framework”.
- **Runtime**: Python \(>= 3.9\)
- **Core dependencies** (from `qa_agent/pyproject.toml`):
  - FastAPI + Uvicorn (web API)
  - Jinja2 (HTML templating)
  - Playwright (browser automation)
  - SQLAlchemy (persistence/data layer)
  - Pydantic + pydantic-settings (models/config)
  - httpx (HTTP client)
  - PyYAML (config)

### Current state observed

- The workspace currently contains local virtual environments (`qa_agent/.venv`, `qa_agent/venv`) and pytest cache (`qa_agent/.pytest_cache`). These should not be committed.

### Next recommended steps

- Add a project `README.md` at the repo root explaining how to install and run the FastAPI service.
- Ensure a single virtualenv convention (prefer `qa_agent/.venv/`) and remove the extra `qa_agent/venv/` if unused.
- Add basic CI (lint/test) once the repo is on GitHub.

