"""Generic data / state assertion models — no product schema; config-driven only."""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field


class DataCheckKind(str, Enum):
    """How the check obtains its subject (DSN and SQL come only from config)."""

    SQL_QUERY = "sql_query"
    JSON_STATE = "json_state"


class DataCheckSpec(BaseModel):
    """
    One structured assertion.

    * **sql_query** — ``connection_ref`` resolves ``plugin_config.connections[ref]`` to a DSN;
      ``sql`` is executed; expectations apply to row set / first row.
    * **json_state** — JSON is loaded from ``inline_document``, ``file_path``, or
      ``metadata_path`` (dotted path into the run metadata snapshot); expectations apply
      to that document.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Stable id for reports.")
    kind: DataCheckKind

    connection_ref: Optional[str] = Field(
        default=None,
        description="Key into plugins.data_validation.connections for sql_query.",
    )
    sql: Optional[str] = Field(default=None, description="SQL text (trusted; from config only).")

    json_source: Optional[str] = Field(
        default=None,
        description="For json_state: inline | file | metadata_path.",
    )
    inline_document: Optional[Any] = Field(default=None, description="Raw JSON value when json_source=inline.")
    file_path: Optional[str] = Field(default=None, description="UTF-8 JSON file when json_source=file.")
    metadata_path: Optional[str] = Field(
        default=None,
        description="Dotted path into run metadata JSON when json_source=metadata_path.",
    )

    expect_min_rows: Optional[int] = Field(default=None, description="sql_query: minimum row count.")
    expect_max_rows: Optional[int] = Field(default=None, description="sql_query: maximum row count.")
    expect_first_row: Optional[Mapping[str, Any]] = Field(
        default=None,
        description="sql_query: first row must contain these column->value pairs (names from config).",
    )

    expect_json_keys: Optional[List[str]] = Field(
        default=None,
        description="json_state: top-level keys that must exist if document is a dict.",
    )
    expect_paths: Optional[Mapping[str, Any]] = Field(
        default=None,
        description="json_state: dotted paths -> values that must equal (==) after navigation.",
    )


class DataValidationCaseResult(BaseModel):
    """Outcome for one :class:`DataCheckSpec`."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    ok: bool
    kind: str
    error: Optional[str] = None
    validation_errors: List[str] = Field(default_factory=list)
    detail: Mapping[str, Any] = Field(default_factory=dict)


class DataValidationSummary(BaseModel):
    """Aggregate for :class:`~qa_agent.core.run_metadata.ValidatorMetadata.data_validation`."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., description="completed | skipped | failed")
    checks_run: int = 0
    checks_passed: int = 0
    failed: Optional[bool] = None
    cases: List[DataValidationCaseResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
