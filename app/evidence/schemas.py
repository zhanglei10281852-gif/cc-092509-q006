from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvidencePackageCreate(BaseModel):
    package_code: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=2, max_length=200)
    subject_kind: Literal["disclosure", "patent_family"]
    subject_reference: str = Field(min_length=2, max_length=100)


class EvidenceEntryAppend(BaseModel):
    import_key: str = Field(min_length=4, max_length=100)
    source_kind: Literal["public_document", "experiment_log", "search_opinion"]
    source_reference: str = Field(min_length=2, max_length=300)
    content: str = Field(min_length=1, max_length=20000)
    captured_at: str = Field(min_length=10, max_length=40)
    citation_scope: str = Field(min_length=1, max_length=300)
    interpretation: str = Field(default="", max_length=2000)


class ObjectionRaise(BaseModel):
    objection_code: str | None = Field(default=None, max_length=64)
    entry_id: int = Field(gt=0)
    reason: str = Field(min_length=4, max_length=2000)


class ObjectionDecision(BaseModel):
    outcome: Literal["upheld", "dismissed"]
    resolution: str = Field(min_length=2, max_length=2000)
