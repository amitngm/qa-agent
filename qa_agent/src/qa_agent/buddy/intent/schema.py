"""Intent classification result schema."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IntentResult:
    """Output of IntentRouter.classify()."""

    intent: str                             # TC_GENERATION | BUG_INVESTIGATION | etc.
    confidence: str                         # HIGH | MEDIUM | LOW
    features: list[str] = field(default_factory=list)   # canonical feature keys
    actions: list[str] = field(default_factory=list)    # create | delete | edit | etc.
    environment: str = "unknown"            # production | qa | staging | dev | unknown
    urgency: str = "normal"                 # critical | high | normal
    requires_live_tools: bool = False
    suggested_tools: list[str] = field(default_factory=list)
    ambiguous: bool = False
    clarification_needed: str = ""

    # ── Convenience helpers ──────────────────

    @property
    def primary_feature(self) -> str:
        return self.features[0] if self.features else "unknown"

    @property
    def is_critical(self) -> bool:
        return self.urgency == "critical"

    @property
    def needs_rag(self) -> bool:
        return self.intent not in {"CLASSIFIER", "SELF_CHECK"}

    def to_prompt_vars(self) -> dict:
        """Return a dict suitable for PromptLibrary.build() kwargs."""
        return {
            "intent": self.intent,
            "features": str(self.features),
            "actions": str(self.actions),
            "environment": self.environment,
            "urgency": self.urgency,
            "feature": self.primary_feature,
            "operations": ", ".join(self.actions) if self.actions else "unspecified",
        }
