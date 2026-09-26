from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EvidenceItemUpsert(BaseModel):
    """登记或导入一条证据。

    同一证据（相同 source_kind + source_ref，或相同幂等键）重复导入只会返回既有节点，
    不会产生重复证据，也不允许借重复导入改写原始内容。
    """

    source_kind: Literal["publication", "lab_record", "search_opinion", "other"]
    source_ref: str = Field(min_length=2, max_length=200, description="稳定来源标识，如 DOI、实验日志编号、检索意见文号")
    title: str = Field(min_length=1, max_length=300)
    published_at: str | None = Field(default=None, max_length=40, description="来源公开时间，ISO-8601")
    content: dict[str, Any] = Field(default_factory=dict, description="证据原始内容，归档后用于核对当时看到的内容")
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=100)


class PackageCreate(BaseModel):
    package_code: str = Field(min_length=3, max_length=64)
    subject_kind: Literal["disclosure", "patent_family"] = Field(description="交底书或专利家族")
    subject_ref: str = Field(min_length=2, max_length=120, description="交底编号或家族编号")
    title: str = Field(min_length=1, max_length=300)
    note: str = Field(default="", max_length=1000)


class EntryAppend(BaseModel):
    """向证据包追加一个条目。

    证据包提交后仍可追加补充材料，但只能追加，不能替换既有条目原文；
    条目的内容摘要、引用范围在入链时固定，后续不可修改。
    """

    evidence_id: int = Field(gt=0)
    summary: str = Field(min_length=4, max_length=2000, description="内容摘要，入链后不可修改")
    quote_range: str = Field(min_length=1, max_length=300, description="引用范围，如页码、段落、权项、图号")
    entry_kind: Literal["primary", "supplement"] = "primary"
    note: str = Field(default="", max_length=1000)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=100)


class ChallengeCreate(BaseModel):
    entry_id: int | None = Field(default=None, gt=0, description="针对某条证据；为空表示针对整个证据包")
    reason: str = Field(min_length=4, max_length=2000)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=100)


class ChallengeWithdraw(BaseModel):
    note: str = Field(default="", max_length=1000)


class ChallengeDecision(BaseModel):
    """裁决异议：维持（upheld）、补正（amended）或驳回（struck）。

    补正必须提供已经追加的补充条目；驳回表示异议不成立，原条目保持有效。
    裁决人不能是提出异议的人。
    """

    outcome: Literal["upheld", "amended", "rejected"]
    decision_note: str = Field(min_length=1, max_length=2000)
    resolution_entry_id: int | None = Field(default=None, gt=0, description="补正裁决所依据的补充条目")

    @model_validator(mode="after")
    def require_resolution_for_amend(self):
        if self.outcome == "amended" and self.resolution_entry_id is None:
            raise ValueError("补正裁决必须提供补充条目 resolution_entry_id")
        return self


class BindingCreate(BaseModel):
    """把同一证据复用到另一个交底或专利家族，并保留各自独立的解释。"""

    evidence_id: int = Field(gt=0)
    subject_kind: Literal["disclosure", "patent_family"]
    subject_ref: str = Field(min_length=2, max_length=120)
    interpretation: str = Field(min_length=4, max_length=4000, description="该交底/家族对这条证据的独立解释")
    note: str = Field(default="", max_length=1000)


class BindingUpdate(BaseModel):
    interpretation: str = Field(min_length=4, max_length=4000)
    note: str = Field(default="", max_length=1000)
