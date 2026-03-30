"""Validation category taxonomy — five pillars including security."""

from __future__ import annotations

from enum import Enum


class ValidationCategory(str, Enum):
    """
    Orthogonal validation dimensions (API, data, UI/UX, state, security).
    """

    API = "api"
    DATA = "data"
    UI = "ui"
    UX = "ux"
    STATE = "state"
    SECURITY = "security"
