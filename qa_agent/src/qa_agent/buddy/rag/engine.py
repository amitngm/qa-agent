"""
RAGEngine — retrieval-augmented generation layer for KaneQA.

Current state: STUB.
Returns domain knowledge from PlatformDomain (same as what buddy_routes used inline).
Interface is stable — replace _retrieve_from_vector_store() to wire a real vector DB
(Weaviate, Qdrant, Chroma, etc.) without changing callers.

Callers:
    buddy_routes.py → rag_engine.retrieve(intent_result, query) → RAGResult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from qa_agent.buddy.domain.platform import PlatformDomain
from qa_agent.buddy.intent.schema import IntentResult

log = logging.getLogger("qa_agent.buddy.rag.engine")

# Sources needed per intent — drives retrieval strategy when a real store is wired.
_INTENT_SOURCES: dict[str, list[str]] = {
    "TC_GENERATION":      ["product_docs", "api_specs", "test_cases"],
    "BUG_INVESTIGATION":  ["jira_tickets", "runbooks", "log_archive"],
    "RELEASE_VALIDATION": ["release_notes", "jira_tickets", "test_cases"],
    "RCA_LOG_ANALYSIS":   ["log_archive", "runbooks", "architecture_docs"],
    "AUTOMATION_GEN":     ["api_specs", "product_docs", "test_cases"],
    "ENV_ISSUE":          ["runbooks", "architecture_docs", "jira_tickets"],
    "FEATURE_UNDERSTAND": ["product_docs", "api_specs", "architecture_docs"],
    "GENERAL_QA":         ["sop_docs", "product_docs"],
}


@dataclass
class RAGResult:
    context: str                              # Assembled text to inject into prompt
    sources_used: list[str] = field(default_factory=list)   # e.g. ["domain:vpc", "domain:subnet"]
    confidence: float = 0.0                   # 0.0–1.0 retrieval quality score
    is_stub: bool = True                      # True until real vector store is wired
    stub_sources_needed: list[str] = field(default_factory=list)  # sources that WOULD improve this answer


class RAGEngine:
    """
    Retrieves relevant knowledge for a classified query.

    Usage:
        rag = RAGEngine()
        result = rag.retrieve(intent_result, user_query)
        # result.context → inject into PromptLibrary.build(rag_context=result.context)
    """

    def retrieve(self, intent_result: IntentResult, query: str) -> RAGResult:
        """
        Retrieve context for the given intent + query.

        Currently returns domain knowledge from PlatformDomain.
        Replace _retrieve_from_vector_store() to use a real embedding store.
        """
        # 1. Try real vector store (stub: always returns None)
        vector_result = self._retrieve_from_vector_store(intent_result, query)
        if vector_result is not None:
            return vector_result

        # 2. Fallback: domain knowledge from PlatformDomain (always available)
        return self._retrieve_from_domain(intent_result)

    def _retrieve_from_domain(self, intent_result: IntentResult) -> RAGResult:
        """Return domain knowledge for detected features. Always available."""
        parts = []
        sources = []
        for feature in intent_result.features[:3]:
            if feature == "unknown":
                continue
            ctx = PlatformDomain.domain_context(feature)
            if ctx:
                parts.append(ctx)
                sources.append(f"domain:{feature}")

        # Sources this intent needs from a real knowledge store (not yet wired)
        sources_needed = _INTENT_SOURCES.get(intent_result.intent.upper(), [])

        if not parts:
            return RAGResult(
                context="(no domain context — feature not recognized in platform taxonomy)",
                sources_used=[],
                confidence=0.1,   # explicit: no feature match, very low signal
                is_stub=True,
                stub_sources_needed=sources_needed,
            )

        if sources_needed:
            parts.append(
                f"\nNote: {intent_result.intent} answers would improve significantly "
                f"with: {', '.join(sources_needed)} "
                f"(not yet connected — wire RAGEngine._retrieve_from_vector_store)"
            )

        return RAGResult(
            context="\n\n".join(parts),
            sources_used=sources,
            # 0.3 = domain taxonomy only, better than nothing but not retrieved evidence
            confidence=0.3,
            is_stub=True,
            stub_sources_needed=sources_needed,
        )

    def _retrieve_from_vector_store(
        self,
        intent_result: IntentResult,  # noqa: ARG002
        query: str,                    # noqa: ARG002
    ) -> RAGResult | None:
        """
        TODO: Replace this with real vector store retrieval.

        Example implementation sketch:
            chunks = self._store.search(
                query=query,
                filters={"feature": intent_result.features, "doc_type": sources_needed},
                top_k=8,
            )
            if not chunks:
                return None
            context = "\n\n".join(c.text for c in chunks)
            sources = [c.source_id for c in chunks]
            confidence = sum(c.score for c in chunks) / len(chunks)
            return RAGResult(context=context, sources_used=sources,
                             confidence=confidence, is_stub=False)
        """
        return None  # stub — always falls through to domain knowledge
