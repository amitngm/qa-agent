"""Run history digests — path-based reader and compatibility with :class:`~qa_agent.store.digest_listing.DigestListingRunHistoryReader`."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from qa_agent.store.digest_listing import scan_runs_directory_for_digests


class StoreBackedRunHistoryReader:
    """
    Implements :class:`~qa_agent.capabilities.future.RunHistoryReader` by scanning a runs root directory.

    Prefer :class:`~qa_agent.store.digest_listing.DigestListingRunHistoryReader` when you have a
    :class:`~qa_agent.store.digest_listing.SupportsRunDigestListing` store; this class remains for
    tests and callers that only hold a filesystem path.
    """

    def __init__(self, runs_root: Path, *, exclude_run_id: Optional[str] = None) -> None:
        self._runs_root = Path(runs_root)
        self._exclude_run_id = exclude_run_id

    def read_recent_digests(self, *, limit: int = 20) -> Sequence[Mapping[str, Any]]:
        """Newest-first digests with stable keys for planner metadata."""
        return scan_runs_directory_for_digests(
            self._runs_root,
            limit=limit,
            exclude_run_id=self._exclude_run_id,
        )
