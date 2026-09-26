from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import current_principal
from app.core.security import Principal
from app.database import get_connection, transaction
from app.evidence.schemas import EvidenceEntryAppend, EvidencePackageCreate, ObjectionDecision, ObjectionRaise
from app.evidence.service import EvidenceObjectionService, EvidencePackageService

router = APIRouter(prefix="/api/evidence", tags=["新颖性证据包"])


@router.post("/packages", status_code=status.HTTP_201_CREATED)
def create_package(payload: EvidencePackageCreate, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).create_package(principal, payload.model_dump())


@router.get("/packages")
def list_packages(
    subject_kind: str | None = Query(default=None),
    subject_reference: str | None = Query(default=None),
    state: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
):
    return EvidencePackageService(get_connection()).list_packages(principal, subject_kind, subject_reference, state)


@router.get("/packages/{package_id}")
def package_detail(package_id: int, principal: Principal = Depends(current_principal)):
    return EvidencePackageService(get_connection()).detail(principal, package_id)


@router.post("/packages/{package_id}/entries", status_code=status.HTTP_201_CREATED)
def append_entry(package_id: int, payload: EvidenceEntryAppend, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).append_entry(principal, package_id, payload.model_dump())


@router.get("/packages/{package_id}/chain")
def package_chain(package_id: int, principal: Principal = Depends(current_principal)):
    return EvidencePackageService(get_connection()).chain(principal, package_id)


@router.get("/packages/{package_id}/verify")
def verify_package_chain(package_id: int, principal: Principal = Depends(current_principal)):
    return EvidencePackageService(get_connection()).verify(principal, package_id)


@router.post("/packages/{package_id}/submit")
def submit_package(package_id: int, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).submit(principal, package_id)


@router.post("/packages/{package_id}/archive")
def archive_package(package_id: int, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidencePackageService(connection).archive(principal, package_id)


@router.post("/packages/{package_id}/objections", status_code=status.HTTP_201_CREATED)
def raise_objection(package_id: int, payload: ObjectionRaise, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidenceObjectionService(connection).raise_objection(principal, package_id, payload.model_dump())


@router.post("/objections/{objection_id}/decide")
def decide_objection(objection_id: int, payload: ObjectionDecision, principal: Principal = Depends(current_principal)):
    with transaction(immediate=True) as connection:
        return EvidenceObjectionService(connection).decide(principal, objection_id, payload.model_dump())


@router.get("/nodes/{node_id}")
def node_detail(node_id: int, principal: Principal = Depends(current_principal)):
    return EvidencePackageService(get_connection()).node_detail(principal, node_id)
