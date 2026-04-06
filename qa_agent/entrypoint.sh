#!/bin/sh
# Start a virtual display so Playwright can run in headed (UI) mode inside the pod.
# headless=False in auto_explore_ui config will use this display.
Xvfb :99 -screen 0 1280x1024x24 -ac &
export DISPLAY=:99
exec uvicorn qa_agent.main:app --host 0.0.0.0 --port 8000 --workers 2 --log-level info
