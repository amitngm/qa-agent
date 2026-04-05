"""
IntentRouter — classifies a user query into one of 8 intent types.

Two-stage strategy (fast + accurate):
  Stage 1: keyword rules (zero latency, zero cost)
            — handles the obvious cases immediately
  Stage 2: LLM call using CLASSIFIER prompt
            — only when keyword rules are ambiguous or confidence is LOW

This design means:
  - "Generate test cases for VPC" → classified instantly via keywords, no LLM call
  - "The dashboard is weird after the latest push" → keyword stage returns AMBIGUOUS,
    LLM stage classifies as BUG_INVESTIGATION

LLM stage uses the cheapest/fastest model — the CLASSIFIER prompt outputs JSON only.
Falls back to GENERAL_QA on any parse error (never blocks the chat stream).
"""

from __future__ import annotations

import json
import logging
import re

from qa_agent.buddy.domain.platform import PlatformDomain
from qa_agent.buddy.intent.schema import IntentResult

log = logging.getLogger("qa_agent.buddy.intent.router")


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Keyword rules
# Each rule is (pattern, intent, confidence, urgency_override | None)
# Evaluated in order — first match wins.
# ─────────────────────────────────────────────────────────────────────────────

_KEYWORD_RULES: list[tuple[re.Pattern, str, str, str | None]] = [
    # Test case generation
    (re.compile(r'\b(generate|create|write|produce|list|give me)\b.{0,40}\btest cases?\b', re.I),
     "TC_GENERATION", "HIGH", None),
    (re.compile(r'\btest cases?\b.{0,40}\b(for|of|covering|on)\b', re.I),
     "TC_GENERATION", "HIGH", None),
    (re.compile(r'\bwhat (should|to) test\b', re.I),
     "TC_GENERATION", "MEDIUM", None),

    # Automation generation
    (re.compile(r'\b(automate|automation plan|playwright|e2e automation|api automation)\b', re.I),
     "AUTOMATION_GEN", "HIGH", None),
    (re.compile(r'\b(write automation|automation script|automate this flow)\b', re.I),
     "AUTOMATION_GEN", "HIGH", None),

    # Release validation
    (re.compile(r'\b(release ready|release readiness|go.no.go|regression scope)\b', re.I),
     "RELEASE_VALIDATION", "HIGH", None),
    (re.compile(r'\b(what (to|should) test (for|before|in) (this )?(release|version))\b', re.I),
     "RELEASE_VALIDATION", "HIGH", None),
    (re.compile(r'\b(release|version)\b.{0,30}\b(ready|readiness|sign.?off|regression)\b', re.I),
     "RELEASE_VALIDATION", "MEDIUM", None),

    # RCA / log analysis
    (re.compile(r'\b(root cause|rca|analyze (the )?logs?|scan (the )?logs?)\b', re.I),
     "RCA_LOG_ANALYSIS", "HIGH", None),
    (re.compile(r'\b(what (caused|happened|went wrong))\b', re.I),
     "RCA_LOG_ANALYSIS", "MEDIUM", None),

    # Bug investigation — critical urgency triggers
    (re.compile(r'\b(production (is )?(down|failing|broken)|outage|data loss)\b', re.I),
     "BUG_INVESTIGATION", "HIGH", "critical"),
    (re.compile(r'\b(p0|critical (bug|issue|failure)|release blocker)\b', re.I),
     "BUG_INVESTIGATION", "HIGH", "critical"),

    # Bug investigation — high urgency
    (re.compile(r'\b(why is .{1,60} (failing|broken|not working|erroring))\b', re.I),
     "BUG_INVESTIGATION", "HIGH", "high"),
    (re.compile(r'\b(investigate|debug|diagnose|what.s wrong with)\b', re.I),
     "BUG_INVESTIGATION", "HIGH", "high"),
    (re.compile(r'\b(error|exception|failure|failing|not working|broken|issue)\b', re.I),
     "BUG_INVESTIGATION", "MEDIUM", "high"),

    # Environment issue
    (re.compile(r'\b(environment (is )?(down|broken|not (starting|responding)))\b', re.I),
     "ENV_ISSUE", "HIGH", "high"),
    (re.compile(r'\b(pod (crash|crashing|restarting|oom|failing)|crashloopbackoff|oomkill|pod is (crash|fail|restart))\b', re.I),
     "ENV_ISSUE", "HIGH", "high"),
    (re.compile(r'\b(crashing|restarting|oomkilled)\b.{0,30}\b(in |on )?(qa|prod|staging|env|namespace)\b', re.I),
     "ENV_ISSUE", "HIGH", "high"),
    (re.compile(r'\b(qa env|staging env|test env).{0,30}\b(down|broken|issue)\b', re.I),
     "ENV_ISSUE", "HIGH", "high"),

    # Feature understanding
    (re.compile(r'\b(how does .{1,60} work|explain .{1,60}|what is .{1,60})\b', re.I),
     "FEATURE_UNDERSTAND", "HIGH", None),
    (re.compile(r'\b(tell me about|describe|overview of|architecture of)\b', re.I),
     "FEATURE_UNDERSTAND", "MEDIUM", None),
]

# Actions extracted from query text
_ACTION_PATTERNS: dict[str, re.Pattern] = {
    "create":  re.compile(r'\b(create|add|provision|launch|spin up|new)\b', re.I),
    "delete":  re.compile(r'\b(delete|remove|destroy|terminate|drop)\b', re.I),
    "edit":    re.compile(r'\b(edit|update|modify|change|rename|patch)\b', re.I),
    "list":    re.compile(r'\b(list|get|show|view|fetch|display)\b', re.I),
    "attach":  re.compile(r'\b(attach|mount|connect|associate|bind)\b', re.I),
    "detach":  re.compile(r'\b(detach|unmount|disconnect|disassociate)\b', re.I),
    "login":   re.compile(r'\b(login|log in|authenticate|auth|token)\b', re.I),
    "deploy":  re.compile(r'\b(deploy|release|rollout|upgrade)\b', re.I),
    "restore": re.compile(r'\b(restore|recover|rollback)\b', re.I),
    "scale":   re.compile(r'\b(scale|resize|extend|expand)\b', re.I),
}

# Environment extraction
_ENV_PATTERNS: dict[str, re.Pattern] = {
    "production": re.compile(r'\b(production|prod)\b', re.I),
    "qa":         re.compile(r'\b(qa|quality assurance|test environment)\b', re.I),
    "staging":    re.compile(r'\b(staging|stage|uat)\b', re.I),
    "dev":        re.compile(r'\b(dev|development|local)\b', re.I),
}

# Live tools needed per intent
_INTENT_TOOLS: dict[str, list[str]] = {
    "BUG_INVESTIGATION": ["scan_namespace_for_issues", "analyze_pod_logs",
                          "k8s_get_env_vars", "k8s_describe_pod"],
    "ENV_ISSUE":         ["k8s_list_pods", "scan_namespace_for_issues",
                          "k8s_get_resource_quota", "http_health_check"],
    "RCA_LOG_ANALYSIS":  ["analyze_pod_logs", "k8s_get_events",
                          "k8s_get_configmap", "k8s_rollout_history"],
}


class IntentRouter:
    """
    Classifies a user query into a structured IntentResult.

    Usage:
        router = IntentRouter(provider=build_provider())  # optional LLM fallback
        result = router.classify("Generate test cases for VPC create/delete")
        # IntentResult(intent="TC_GENERATION", features=["vpc"], actions=["create","delete"])
    """

    def __init__(self, provider=None) -> None:
        """
        provider: optional BaseProvider for LLM-based fallback classification.
        If None, keyword-only classification is used (still good for 80% of cases).
        """
        self._provider = provider

    def classify(self, query: str) -> IntentResult:
        """Classify query. Returns IntentResult. Never raises — defaults to GENERAL_QA."""
        try:
            return self._classify(query)
        except Exception as e:
            log.warning("intent classification failed: %s — defaulting to GENERAL_QA", e)
            return IntentResult(
                intent="GENERAL_QA",
                confidence="LOW",
                features=PlatformDomain.resolve(query),
            )

    def _classify(self, query: str) -> IntentResult:
        # Extract features and actions from query text (always runs)
        features = PlatformDomain.resolve(query)
        actions = self._extract_actions(query)
        environment = self._extract_environment(query)

        # Stage 1: keyword rules
        intent, confidence, urgency = self._keyword_classify(query)

        if confidence == "HIGH":
            # Keyword match was decisive — no LLM call needed
            requires_live = intent in _INTENT_TOOLS
            return IntentResult(
                intent=intent,
                confidence=confidence,
                features=features,
                actions=actions,
                environment=environment,
                urgency=urgency,
                requires_live_tools=requires_live,
                suggested_tools=_INTENT_TOOLS.get(intent, []),
            )

        # Stage 2: LLM fallback (only when keyword confidence is LOW/MEDIUM)
        if self._provider is not None:
            llm_result = self._llm_classify(query)
            if llm_result is not None:
                # Merge: LLM intent + keyword-extracted features/actions
                if not llm_result.features:
                    llm_result.features = features
                if not llm_result.actions:
                    llm_result.actions = actions
                if llm_result.environment == "unknown":
                    llm_result.environment = environment
                return llm_result

        # Fallback: return keyword result even if MEDIUM/LOW
        requires_live = intent in _INTENT_TOOLS
        return IntentResult(
            intent=intent,
            confidence=confidence,
            features=features,
            actions=actions,
            environment=environment,
            urgency=urgency,
            requires_live_tools=requires_live,
            suggested_tools=_INTENT_TOOLS.get(intent, []),
        )

    def _keyword_classify(self, query: str) -> tuple[str, str, str]:
        """Returns (intent, confidence, urgency)."""
        for pattern, intent, confidence, urgency_override in _KEYWORD_RULES:
            if pattern.search(query):
                urgency = urgency_override or "normal"
                return intent, confidence, urgency
        return "GENERAL_QA", "LOW", "normal"

    def _extract_actions(self, query: str) -> list[str]:
        return [action for action, pattern in _ACTION_PATTERNS.items()
                if pattern.search(query)]

    def _extract_environment(self, query: str) -> str:
        for env, pattern in _ENV_PATTERNS.items():
            if pattern.search(query):
                return env
        return "unknown"

    def _llm_classify(self, query: str) -> IntentResult | None:
        """Call LLM with CLASSIFIER prompt, parse JSON result."""
        from qa_agent.buddy.reasoning.prompts import PromptLibrary
        try:
            prompt = PromptLibrary.build("CLASSIFIER", user_query=query)
            response = self._provider.chat(
                messages=[{"role": "user", "content": query}],
                tools=[],
                system_prompt=prompt,
                max_tokens=512,
            )
            # Extract text from response
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text or ""

            # Parse JSON — strip markdown fences if present
            text = re.sub(r"```(?:json)?", "", text).strip()
            data = json.loads(text)

            return IntentResult(
                intent=data.get("intent", "GENERAL_QA"),
                confidence=data.get("confidence", "LOW"),
                features=data.get("features", []),
                actions=data.get("actions", []),
                environment=data.get("environment", "unknown"),
                urgency=data.get("urgency", "normal"),
                requires_live_tools=data.get("requires_live_tools", False),
                suggested_tools=data.get("suggested_tools", []),
                ambiguous=data.get("ambiguous", False),
                clarification_needed=data.get("clarification_needed", ""),
            )
        except Exception as e:
            log.debug("llm classifier parse failed: %s", e)
            return None
