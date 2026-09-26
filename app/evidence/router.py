from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from app.api.dependencies import current_principal
from app.core.security import Principal
from app.database import get_connection, transaction
from app.evidence.schemas import (
    BindingCreate,
    BindingUpdate,
    ChallengeCreate,
    ChallengeDecision,
    ChallengeWithdraw,
    EntryAppend,
    EvidenceItemUpsert,
    PackageCreate,
)
from app.evidence.service import EvidenceItemService, EvidencePackageService
from app.evidence.verification import build_report

router = APIRouter(prefix="/api/evidence", tags=["新颖性证据包"])


# ---- 证据节点 ----

@router.post("/items", status_code=status.HTTP_201_CREATED)
def upsert_evidence_item(payload: EvidenceItemUpsert, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidenceItemService(connection).upsert(principal, payload.model_dump())


@router.get("/items")
def list_evidence_items(
    source_kind: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
):
    return EvidenceItemService(get_connection()).list(principal, source_kind)


@router.get("/items/{evidence_id}")
def get_evidence_item(evidence_id: int, principal: Principal = Depends(current_principal)):
    return EvidenceItemService(get_connection()).get(principal, evidence_id)


# ---- 证据包与哈希链 ----

@router.post("/packages", status_code=status.HTTP_201_CREATED)
def create_package(payload: PackageCreate, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).create_package(principal, payload.model_dump())


@router.get("/packages")
def list_packages(
    subject_kind: str | None = Query(default=None),
    subject_ref: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
):
    return EvidencePackageService(get_connection()).list_packages(principal, subject_kind, subject_ref)


@router.get("/packages/{package_id}")
def get_package(package_id: int, principal: Principal = Depends(current_principal)):
    return EvidencePackageService(get_connection()).detail(principal, package_id)


@router.post("/packages/{package_id}/entries", status_code=status.HTTP_201_CREATED)
def append_entry(package_id: int, payload: EntryAppend, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).append_entry(principal, package_id, payload.model_dump())


@router.post("/packages/{package_id}/submit")
def submit_package(package_id: int, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).submit(principal, package_id)


@router.post("/packages/{package_id}/archive")
def archive_package(package_id: int, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).archive(principal, package_id)


# ---- 异议与裁决 ----

@router.post("/packages/{package_id}/challenges", status_code=status.HTTP_201_CREATED)
def raise_challenge(package_id: int, payload: ChallengeCreate, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).raise_challenge(principal, package_id, payload.model_dump())


@router.post("/challenges/{challenge_id}/decision")
def decide_challenge(challenge_id: int, payload: ChallengeDecision, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).decide_challenge(principal, challenge_id, payload.model_dump())


@router.post("/challenges/{challenge_id}/withdraw")
def withdraw_challenge(challenge_id: int, payload: ChallengeWithdraw, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).withdraw_challenge(principal, challenge_id, payload.note)


# ---- 跨交底/专利家族复用 ----

@router.post("/packages/{package_id}/bindings", status_code=status.HTTP_201_CREATED)
def create_binding(package_id: int, payload: BindingCreate, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).create_binding(principal, package_id, payload.model_dump())


@router.patch("/bindings/{binding_id}")
def update_binding(binding_id: int, payload: BindingUpdate, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).update_binding(principal, binding_id, payload.model_dump())


@router.get("/bindings")
def list_bindings(
    subject_kind: str = Query(...),
    subject_ref: str = Query(..., min_length=2),
    principal: Principal = Depends(current_principal),
):
    return EvidencePackageService(get_connection()).list_bindings_by_subject(principal, subject_kind, subject_ref)


# ---- 完整性校验 ----

@router.get("/verification")
def verify_evidence_chains(principal: Principal = Depends(current_principal)):
    """校验全部证据链。发现断链或摘要不匹配时返回 422，并在 findings 中逐条列出。"""
    principal.require("evidence.verify")
    report = build_report(get_connection())
    if not report["ok"]:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=report)
    return report
