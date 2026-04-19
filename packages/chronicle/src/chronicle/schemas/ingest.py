"""Pydantic schemas for the ingest endpoint (POST /api/v1/runs)."""

from uuid import UUID

from pydantic import BaseModel, field_validator


class IngestRequest(BaseModel):
    """The raw ``test-report-v1`` JSON body.

    Level 1 (Pydantic) validates structural constraints: ``schema_version``
    must start with ``"1"``, and the top-level keys must be present.

    Level 2 (JSON Schema) validation against ``test-report-v1.json`` happens
    in ``IngestService._validate_report()``, not here — because the full
    schema is complex and Pydantic would require duplicating every nested
    type.  The raw ``run`` and ``tests`` dicts are passed through for JSON
    Schema validation and then normalised by the service.
    """

    schema_version: str
    run: dict
    tests: list[dict]

    @field_validator("schema_version")
    @classmethod
    def must_be_v1(cls, v: str) -> str:
        if not v.startswith("1"):
            raise ValueError(f"schema_version '{v}' is not supported; expected '1' or '1.x'")
        return v


class IngestResponse(BaseModel):
    """Returned from ``POST /api/v1/runs``."""

    run_id: UUID
    duplicate: bool = False
    handles_ingested: int
    scenarios_ingested: int
    warning: str | None = None
