"""
SelfCheck — pre-response evidence sufficiency evaluator for KaneQA.

Runs after RAG retrieval, before Brain.chat().
Determines whether the available evidence is sufficient to answer safely.

Decisions:
    PROCEED               — evidence is sufficient, answer normally
    PROCEED_WITH_CAVEAT   — answer but surface a confidence warning to the user
    INSUFFICIENT_EVIDENCE — tell the user what is missing before attempting an answer

buddy_routes.py emits a {type: "caveat"} SSE event if decision != PROCEED.
"""

from __future__ import annotations

from dataclasses import dataclass

from qa_agent.buddy.intent.schema import IntentResult
from qa_agent.buddy.rag.engine import RAGResult

PROCEED = "PROCEED"
PROCEED_WITH_CAVEAT = "PROCEED_WITH_CAVEAT"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass
class SelfCheckResult:
    decision: str                # PROCEED | PROCEED_WITH_CAVEAT | INSUFFICIENT_EVIDENCE
    caveat_message: str = ""     # Shown to user when decision != PROCEED
    missing: list[str] = None    # What evidence would improve confidence

    def __post_init__(self):
        if self.missing is None:
            self.missing = []

    @property
    def should_proceed(self) -> bool:
        return self.decision in {PROCEED, PROCEED_WITH_CAVEAT}


class SelfCheck:
    """
    Evaluate whether the available evidence is sufficient for the requested intent.

    Usage:
        result = SelfCheck.evaluate(intent_result, rag_result)
        if not result.should_proceed:
            # emit {type: "caveat"} and optionally short-circuit
    """

    @staticmethod
    def evaluate(intent_result: IntentResult, rag_result: RAGResult) -> SelfCheckResult:
        """
        Rules:
        - RELEASE_VALIDATION without Jira/release notes → INSUFFICIENT_EVIDENCE
        - BUG_INVESTIGATION on production with only domain context → PROCEED_WITH_CAVEAT
        - RCA_LOG_ANALYSIS without logs → PROCEED_WITH_CAVEAT
        - Critical urgency with stub RAG → PROCEED_WITH_CAVEAT
        - Everything else with any context → PROCEED
        """
        intent = intent_result.intent.upper()
        urgency = intent_result.urgency
        rag_is_stub = rag_result.is_stub
        rag_confidence = rag_result.confidence

        # Release validation: always needs real evidence
        if intent == "RELEASE_VALIDATION":
            has_real_sources = not rag_is_stub and rag_confidence >= 0.6
            if not has_real_sources:
                return SelfCheckResult(
                    decision=INSUFFICIENT_EVIDENCE,
                    caveat_message=(
                        "Release readiness assessment requires real evidence. "
                        "Please provide: test execution report, open Jira tickets, "
                        "and release notes. Responding from domain knowledge only "
                        "cannot produce a reliable GO / NO-GO decision."
                    ),
                    missing=["test_execution_report", "jira_open_tickets", "release_notes"],
                )

        # Critical urgency on production: always warn if evidence is stub
        if urgency == "critical" and rag_is_stub:
            return SelfCheckResult(
                decision=PROCEED_WITH_CAVEAT,
                caveat_message=(
                    "[CRITICAL URGENCY] Responding from domain knowledge only — "
                    "no Jira tickets, logs, or runbooks retrieved. "
                    "Connect live tools (K8s, log analysis) and share error logs "
                    "for a precise RCA."
                ),
                missing=["live_logs", "jira_incident", "runbook"],
            )

        # Bug investigation on production without real evidence
        if intent == "BUG_INVESTIGATION" and intent_result.environment == "production" and rag_is_stub:
            return SelfCheckResult(
                decision=PROCEED_WITH_CAVEAT,
                caveat_message=(
                    "Investigating a production issue from domain knowledge only. "
                    "For precise root cause: share pod logs, error traces, or Jira ticket. "
                    "Use live K8s tools if environment access is available."
                ),
                missing=["error_logs", "jira_ticket", "k8s_pod_describe"],
            )

        # RCA without logs
        if intent == "RCA_LOG_ANALYSIS" and rag_is_stub:
            return SelfCheckResult(
                decision=PROCEED_WITH_CAVEAT,
                caveat_message=(
                    "No logs or incident history retrieved. RCA will be based on "
                    "known failure patterns for this feature. Attach logs or a Jira "
                    "incident for a grounded causal chain."
                ),
                missing=["pod_logs", "event_logs", "jira_incident"],
            )

        # All other intents: low RAG confidence but not critical
        if rag_confidence < 0.4 and not intent_result.features:
            return SelfCheckResult(
                decision=PROCEED_WITH_CAVEAT,
                caveat_message=(
                    "No specific platform feature was detected in your query. "
                    "Answering from general QA knowledge. Mention the feature name "
                    "(e.g. 'VPC', 'Load Balancer', 'VM') for targeted guidance."
                ),
                missing=["feature_specification"],
            )

        return SelfCheckResult(decision=PROCEED)
