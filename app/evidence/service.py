from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import Principal
from app.evidence.chain import entry_chain_digest, evidence_content_digest
from app.evidence.repository import EvidenceRepository
from app.evidence.verification import verify_package
from app.services.audit import AuditService


def _objection_code() -> str:
    return f"OBJ-{uuid.uuid4().hex[:12]}"


def _evidence_code() -> str:
    return f"EV-{uuid.uuid4().hex[:12]}"


class EvidencePackageService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None):
        self.connection = connection
        self.clock = clock or SystemClock()
        self.repository = EvidenceRepository(connection)
        self.audit = AuditService(connection, self.clock)

    def create_package(self, principal: Principal, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        if self.repository.package_by_code(data["package_code"]):
            raise ConflictError("证据包编号已经存在")
        now = to_storage(self.clock.now())
        package = self.repository.create_package(data, principal.user_id, now)
        self.repository.append_event(package["id"], "package.created", principal.user_id, now, {"package_code": package["package_code"]})
        self.audit.record(principal, "evidence_package.create", "evidence_package", str(package["id"]), after=package)
        return package

    def list_packages(self, principal: Principal, subject_kind: str | None, subject_reference: str | None, state: str | None) -> list[dict[str, Any]]:
        principal.require("evidence.read")
        return self.repository.list_packages(subject_kind=subject_kind, subject_reference=subject_reference, state=state)

    def detail(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.read")
        package = self.repository.get_package(package_id)
        package["entries"] = self.repository.entries(package_id)
        package["objections"] = self.repository.objections_for_package(package_id)
        package["events"] = self.repository.events(package_id)
        return package

    def append_entry(self, principal: Principal, package_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        package = self.repository.get_package(package_id)
        now = to_storage(self.clock.now())
        content_digest = evidence_content_digest(data["source_kind"], data["source_reference"], data["content"])
        existing = self.repository.entry_by_import_key(package_id, data["import_key"])
        if existing is not None:
            same_request = (
                existing["content_digest"] == content_digest
                and existing["citation_scope"] == data["citation_scope"]
                and existing["interpretation"] == data["interpretation"]
            )
            if not same_request:
                raise ConflictError("同一导入键不能用于不同请求")
            return {"entry": self._entry_view(existing["id"]), "replayed": True, "evidence_reused": True}
        if package["state"] == "archived":
            raise ConflictError("证据包已归档，禁止追加或改动任何条目")
        node = self.repository.node_by_digest(content_digest)
        evidence_reused = node is not None
        if node is None:
            node = self.repository.create_node(
                {
                    "evidence_code": _evidence_code(),
                    "source_kind": data["source_kind"],
                    "source_reference": data["source_reference"],
                    "content": data["content"],
                    "content_digest": content_digest,
                    "captured_at": data["captured_at"],
                },
                principal.user_id,
                now,
            )
        last = self.repository.last_entry(package_id)
        position = 1 if last is None else int(last["position"]) + 1
        prev_entry_digest = "" if last is None else last["entry_digest"]
        digest = entry_chain_digest(
            package_code=package["package_code"],
            position=position,
            content_digest=content_digest,
            citation_scope=data["citation_scope"],
            interpretation=data["interpretation"],
            prev_entry_digest=prev_entry_digest,
            appended_at=now,
        )
        entry = self.repository.append_entry(
            {
                "package_id": package_id,
                "evidence_id": node["id"],
                "position": position,
                "citation_scope": data["citation_scope"],
                "interpretation": data["interpretation"],
                "content_digest": content_digest,
                "prev_entry_digest": prev_entry_digest,
                "entry_digest": digest,
                "import_key": data["import_key"],
            },
            principal.user_id,
            now,
        )
        self.repository.append_event(
            package_id,
            "entry.appended",
            principal.user_id,
            now,
            {"entry_id": entry["id"], "position": position, "evidence_id": node["id"], "evidence_reused": evidence_reused},
        )
        self.audit.record(
            principal,
            "evidence_entry.append",
            "evidence_package",
            str(package_id),
            after={"entry_id": entry["id"], "position": position, "entry_digest": digest},
        )
        return {"entry": self._entry_view(entry["id"]), "replayed": False, "evidence_reused": evidence_reused}

    def submit(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.write")
        package = self.repository.get_package(package_id)
        if package["state"] != "draft":
            raise ConflictError("只有草稿状态的证据包可以提交")
        if self.repository.entry_count(package_id) == 0:
            raise ValidationError("证据包至少包含一条证据条目才能提交")
        now = to_storage(self.clock.now())
        updated = self.repository.mark_submitted(package_id, now)
        self.repository.append_event(package_id, "package.submitted", principal.user_id, now, {"entry_count": self.repository.entry_count(package_id)})
        self.audit.record(principal, "evidence_package.submit", "evidence_package", str(package_id), before=package, after=updated)
        return updated

    def archive(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.adjudicate")
        package = self.repository.get_package(package_id)
        if package["state"] != "submitted":
            raise ConflictError("只有已提交的证据包可以归档")
        pending = self.repository.pending_objection_count(package_id)
        if pending:
            raise ConflictError("仍存在未裁决的异议，不能归档", context={"pending_objections": pending})
        now = to_storage(self.clock.now())
        updated = self.repository.mark_archived(package_id, now)
        self.repository.append_event(package_id, "package.archived", principal.user_id, now)
        self.audit.record(principal, "evidence_package.archive", "evidence_package", str(package_id), before=package, after=updated)
        return updated

    def chain(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.read")
        package = self.repository.get_package(package_id)
        return {
            "package_id": package["id"],
            "package_code": package["package_code"],
            "state": package["state"],
            "ordered_by": "position",
            "entries": self.repository.entries(package_id),
        }

    def verify(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.read")
        self.repository.get_package(package_id)
        return verify_package(self.connection, package_id)

    def node_detail(self, principal: Principal, node_id: int) -> dict[str, Any]:
        principal.require("evidence.read")
        node = self.repository.get_node(node_id)
        node["references"] = self.repository.node_references(node_id)
        return node

    def _entry_view(self, entry_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            """SELECT e.*,n.evidence_code,n.source_kind,n.source_reference,n.captured_at
               FROM evidence_package_entries e JOIN evidence_nodes n ON n.id=e.evidence_id
               WHERE e.id=?""",
            (entry_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError("证据条目不存在")
        return dict(row)


class EvidenceObjectionService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None):
        self.connection = connection
        self.clock = clock or SystemClock()
        self.repository = EvidenceRepository(connection)
        self.audit = AuditService(connection, self.clock)

    def raise_objection(self, principal: Principal, package_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        package = self.repository.get_package(package_id)
        if package["state"] != "submitted":
            raise ConflictError("只有已提交且未归档的证据包可以接受异议")
        entry = self.repository.get_entry(data["entry_id"])
        if entry["package_id"] != package_id:
            raise ValidationError("异议条目不属于该证据包")
        objection_code = data.get("objection_code") or _objection_code()
        if self.repository.objection_by_code(objection_code):
            raise ConflictError("异议编号已经存在")
        now = to_storage(self.clock.now())
        objection = self.repository.create_objection(
            {"objection_code": objection_code, "package_id": package_id, "entry_id": data["entry_id"], "reason": data["reason"]},
            principal.user_id,
            now,
        )
        self.repository.append_event(package_id, "objection.raised", principal.user_id, now, {"objection_id": objection["id"], "entry_id": data["entry_id"]})
        self.audit.record(principal, "evidence_objection.raise", "evidence_package", str(package_id), after=objection)
        return objection

    def decide(self, principal: Principal, objection_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.adjudicate")
        objection = self.repository.get_objection(objection_id)
        if objection["state"] != "pending":
            raise ConflictError("该异议已经裁决，不能重复处理")
        if objection["raised_by"] == principal.user_id:
            raise ConflictError("裁决人不能是异议提出人")
        now = to_storage(self.clock.now())
        decided = self.repository.decide_objection(objection_id, data["outcome"], data["resolution"], principal.user_id, now)
        self.repository.append_event(
            objection["package_id"],
            "objection.decided",
            principal.user_id,
            now,
            {"objection_id": objection_id, "outcome": data["outcome"]},
        )
        self.audit.record(principal, "evidence_objection.decide", "evidence_objection", str(objection_id), before=objection, after=decided)
        return decided
