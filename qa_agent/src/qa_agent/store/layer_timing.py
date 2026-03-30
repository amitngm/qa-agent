"""Major pipeline phases for aggregated layer timing (observability)."""

from __future__ import annotations

from enum import Enum


class MajorLayer(str, Enum):
    """
    Logical phases for timing — multiple pipeline keys may roll up into one phase
    (e.g. validation plugins → ``validator``).
    """

    PLANNER = "planner"
    DISCOVERER = "discoverer"
    EXECUTOR = "executor"
    VALIDATOR = "validator"
    ANALYZER = "analyzer"
    REPORTER = "reporter"
