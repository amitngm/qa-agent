"""Entry point for local development: `uvicorn qa_agent.main:app --reload`."""

from qa_agent.api.app import create_app

app = create_app()
