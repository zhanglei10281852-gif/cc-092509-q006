from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import Principal
from app.evidence.chain import archive_digest, digest_content, genesis_hash, hash_entry
from app.evidence.repository import (
    BindingRepository,
    ChallengeRepository,
    EvidenceItemRepository,
    EvidencePackageRepository,
)
from app.services.audit import AuditService


class EvidenceItemService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None):
        self.connection = connection
        self.clock = clock or SystemClock()
        self.items = EvidenceItemRepository(connection)
        self.audit = AuditService(connection, self.clock)

    def upsert(self, principal: Principal, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        now = to_storage(self.clock.now())
        fingerprint = digest_content(data["content"])
        key = data.get("idempotency_key")

        keyed = self.items.by_idempotency_key(key) if key else None
        natural = self.items.by_natural_key(data["source_kind"], data["source_ref"])

        if keyed is not None and natural is not None and keyed["id"] != natural["id"]:
            raise ConflictError("幂等键与来源标识分别指向不同证据节点，拒绝导入")
        existing = natural or keyed
        if existing is not None:
            if existing["content_digest"] != fingerprint:
                raise ConflictError(
                    "证据节点已存在但内容摘要不一致，重复导入不能替换登记时看到的原文",
                    context={"evidence_id": existing["id"]},
                )
            return {"item": existing, "replayed": True}

        try:
            item = self.items.create(data, fingerprint, principal.user_id, now)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("证据或幂等键已经存在，导入未产生重复节点") from exc
        self.audit.record(principal, "evidence.import", "evidence_item", str(item["id"]), after={
            "source_kind": item["source_kind"], "source_ref": item["source_ref"],
            "content_digest": item["content_digest"],
        })
        return {"item": item, "replayed": False}

    def get(self, principal: Principal, evidence_id: int) -> dict[str, Any]:
        principal.require("evidence.read")
        return self.items.get(evidence_id)

    def list(self, principal: Principal, source_kind: str | None) -> list[dict[str, Any]]:
        principal.require("evidence.read")
        return self.items.list(source_kind=source_kind)


class EvidencePackageService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None):
        self.connection = connection
        self.clock = clock or SystemClock()
        self.packages = EvidencePackageRepository(connection)
        self.items = EvidenceItemRepository(connection)
        self.challenges = ChallengeRepository(connection)
        self.bindings = BindingRepository(connection)
        self.audit = AuditService(connection, self.clock)

    # ---- 证据包 ----

    def create_package(self, principal: Principal, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        if self.packages.by_code(data["package_code"]):
            raise ConflictError("证据包编码已经存在")
        now = to_storage(self.clock.now())
        package = self.packages.create(data, now)
        self.packages.add_event(package["id"], "package.created", principal.user_id,
                                f"创建证据包 {package['package_code']}", data, now)
        self.audit.record(principal, "evidence_package.create", "evidence_package", str(package["id"]), after=package)
        return package

    def list_packages(self, principal: Principal, subject_kind: str | None, subject_ref: str | None) -> list[dict[str, Any]]:
        principal.require("evidence.read")
        return self.packages.list(subject_kind=subject_kind, subject_ref=subject_ref)

    def detail(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.read")
        package = self.packages.get(package_id)
        package["entries"] = self._entries_with_source(package_id)
        package["challenges"] = self.challenges.list_for_package(package_id)
        package["bindings"] = self.bindings.list_for_package(package_id)
        package["events"] = self.packages.events(package_id)
        return package

    def _entries_with_source(self, package_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT e.*, i.source_kind, i.source_ref, i.title AS evidence_title, i.published_at
               FROM package_entries e JOIN evidence_items i ON i.id=e.evidence_id
               WHERE e.package_id=? ORDER BY e.seq""",
            (package_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ---- 条目（只追加的哈希链） ----

    def append_entry(self, principal: Principal, package_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        package = self.packages.get(package_id)
        if package["state"] == "archived":
            raise ConflictError("证据包已归档，只能检索不能追加或修改")
        evidence = self.items.get(data["evidence_id"])
        now = to_storage(self.clock.now())
        key = data.get("idempotency_key")

        if key:
            replayed = self.packages.find_entry_by_idempotency_key(package_id, key)
            if replayed is not None:
                self._assert_same_append(replayed, data)
                return {"entry": replayed, "package": self.packages.get(package_id), "replayed": True}

        seq = self.packages.next_seq(package_id)
        prev_hash = package["head_digest"] or genesis_hash(package["package_code"])
        entry_hash = hash_entry(
            package_code=package["package_code"],
            seq=seq,
            entry_kind=data["entry_kind"],
            evidence_id=evidence["id"],
            content_digest=evidence["content_digest"],
            quote_range=data["quote_range"],
            summary=data["summary"],
            note=data.get("note", ""),
            appended_by=principal.user_id,
            created_at=now,
            prev_hash=prev_hash,
        )
        entry = self.packages.append_entry(
            package_id=package_id,
            evidence_id=evidence["id"],
            seq=seq,
            entry_kind=data["entry_kind"],
            quote_range=data["quote_range"],
            summary=data["summary"],
            content_digest=evidence["content_digest"],
            note=data.get("note", ""),
            idempotency_key=key,
            appended_by=principal.user_id,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            now=now,
        )
        self.packages.add_event(
            package_id, "entry.appended", principal.user_id,
            f"追加第 {seq} 条证据（{data['entry_kind']}，{evidence['source_ref']}）",
            {"seq": seq, "evidence_id": evidence["id"], "entry_hash": entry_hash},
            now,
        )
        self.audit.record(principal, "evidence_entry.append", "evidence_package", str(package_id), after={
            "entry_id": entry["id"], "seq": seq, "evidence_id": evidence["id"], "entry_hash": entry_hash,
        })
        return {"entry": entry, "package": self.packages.get(package_id), "replayed": False}

    @staticmethod
    def _assert_same_append(existing: dict[str, Any], data: dict[str, Any]) -> None:
        for field, label in (
            ("evidence_id", "证据"),
            ("summary", "内容摘要"),
            ("quote_range", "引用范围"),
            ("entry_kind", "条目类型"),
        ):
            if existing[field] != data[field]:
                raise ValidationError(f"重放追加请求的{label}与原条目不一致，禁止借幂等重放替换原文")

    # ---- 提交与归档 ----

    def submit(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.write")
        package = self.packages.get(package_id)
        if package["state"] == "archived":
            raise ConflictError("证据包已归档，不能重复提交")
        if package["state"] == "submitted":
            return {"package": package, "replayed": True}
        entries = self.packages.entries(package_id)
        if not entries:
            raise ValidationError("证据包没有任何条目，不能提交")
        now = to_storage(self.clock.now())
        updated = self.packages.mark_submitted(package_id, principal.user_id, now)
        self.packages.add_event(package_id, "package.submitted", principal.user_id,
                                f"提交证据包，共 {len(entries)} 条，链头 {updated['head_digest'][:12]}",
                                {"entry_count": len(entries), "head_digest": updated["head_digest"]}, now)
        self.audit.record(principal, "evidence_package.submit", "evidence_package", str(package_id),
                          before=package, after=updated)
        return {"package": updated, "replayed": False}

    def archive(self, principal: Principal, package_id: int) -> dict[str, Any]:
        principal.require("evidence.archive")
        package = self.packages.get(package_id)
        if package["state"] == "archived":
            return {"package": package, "replayed": True}
        if package["state"] != "submitted":
            raise ConflictError("证据包尚未提交，不能归档")
        open_challenges = self.challenges.open_count(package_id)
        if open_challenges:
            raise ConflictError(f"仍有 {open_challenges} 条异议未裁决，不能归档")
        entries = self.packages.entries(package_id)
        now = to_storage(self.clock.now())
        digest = archive_digest(package["package_code"], len(entries), package["head_digest"])
        updated = self.packages.mark_archived(package_id, principal.user_id, digest, now)
        self.packages.add_event(package_id, "package.archived", principal.user_id,
                                f"归档证据包，归档摘要 {digest[:12]}",
                                {"entry_count": len(entries), "archive_digest": digest}, now)
        self.audit.record(principal, "evidence_package.archive", "evidence_package", str(package_id),
                          before=package, after=updated)
        return {"package": updated, "replayed": False}

    # ---- 异议与裁决 ----

    def raise_challenge(self, principal: Principal, package_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.challenge")
        package = self.packages.get(package_id)
        if package["state"] == "archived":
            raise ConflictError("证据包已归档，不能再提出异议")
        entry_id = data.get("entry_id")
        if entry_id is not None:
            entry = self.packages.get_entry(entry_id)
            if entry["package_id"] != package_id:
                raise ValidationError("异议条目不属于该证据包")
        now = to_storage(self.clock.now())
        key = data.get("idempotency_key")
        if key:
            replayed = self.challenges.find_by_idempotency_key(package_id, key)
            if replayed is not None:
                if replayed["entry_id"] != entry_id or replayed["reason"] != data["reason"]:
                    raise ValidationError("重放异议请求的内容与原异议不一致")
                return {"challenge": replayed, "replayed": True}

        challenge_code = f"CHL-{uuid.uuid4().hex[:12]}"
        challenge = self.challenges.create(
            package_id=package_id, entry_id=entry_id, reason=data["reason"],
            raised_by=principal.user_id, idempotency_key=key, now=now,
        )
        target = f"条目 {entry_id}" if entry_id else "整个证据包"
        self.packages.add_event(package_id, "challenge.raised", principal.user_id,
                                f"对{target}提出异议（{challenge_code}）",
                                {"challenge_id": challenge["id"], "entry_id": entry_id}, now)
        self.audit.record(principal, "evidence_challenge.raise", "evidence_challenge", str(challenge["id"]),
                          after=challenge, metadata={"challenge_code": challenge_code})
        return {"challenge": challenge, "replayed": False}

    def withdraw_challenge(self, principal: Principal, challenge_id: int, note: str) -> dict[str, Any]:
        principal.require("evidence.challenge")
        challenge = self.challenges.get(challenge_id)
        if challenge["raised_by"] != principal.user_id and not principal.can("evidence.decide"):
            raise ConflictError("只能由异议提出人撤回自己的异议")
        now = to_storage(self.clock.now())
        updated = self.challenges.withdraw(challenge_id, now)
        self.packages.add_event(challenge["package_id"], "challenge.withdrawn", principal.user_id,
                                f"撤回异议 {challenge_id}", {"note": note}, now)
        self.audit.record(principal, "evidence_challenge.withdraw", "evidence_challenge", str(challenge_id),
                          before=challenge, after=updated)
        return updated

    def decide_challenge(self, principal: Principal, challenge_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.decide")
        challenge = self.challenges.get(challenge_id)
        if challenge["state"] != "open":
            raise ConflictError("异议已经裁决或撤回")
        if challenge["raised_by"] == principal.user_id:
            raise ValidationError("裁决人不能是异议提出人，异议处理必须职责分离")
        outcome = data["outcome"]
        resolution_entry_id = data.get("resolution_entry_id")
        if resolution_entry_id is not None:
            resolution = self.packages.get_entry(resolution_entry_id)
            if resolution["package_id"] != challenge["package_id"]:
                raise ValidationError("补正条目不属于该证据包")
            if resolution["entry_kind"] != "supplement":
                raise ValidationError("补正裁决必须引用追加的补充条目")
            if challenge["entry_id"] is not None and resolution["evidence_id"] != self.packages.get_entry(challenge["entry_id"])["evidence_id"]:
                raise ValidationError("补正条目必须与被异议条目对应同一证据")
        now = to_storage(self.clock.now())
        updated = self.challenges.decide(
            challenge_id,
            state=outcome,
            decided_by=principal.user_id,
            decision_note=data["decision_note"],
            resolution_entry_id=resolution_entry_id,
            now=now,
        )
        result_labels = {"upheld": "异议成立，原条目不再作为有效依据",
                         "amended": "异议成立，以补充条目补正",
                         "rejected": "异议驳回，原条目维持有效"}
        self.packages.add_event(challenge["package_id"], "challenge.decided", principal.user_id,
                                f"裁决异议 {challenge_id}：{result_labels[outcome]}",
                                {"challenge_id": challenge_id, "outcome": outcome,
                                 "resolution_entry_id": resolution_entry_id}, now)
        self.audit.record(principal, "evidence_challenge.decide", "evidence_challenge", str(challenge_id),
                          before=challenge, after=updated)
        return updated

    # ---- 跨交底/家族复用 ----

    def create_binding(self, principal: Principal, package_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        package = self.packages.get(package_id)
        evidence = self.items.get(data["evidence_id"])
        owns = self.connection.execute(
            "SELECT 1 FROM package_entries WHERE package_id=? AND evidence_id=? LIMIT 1",
            (package_id, evidence["id"]),
        ).fetchone()
        if not owns:
            raise ValidationError("只能复用已经进入本证据包哈希链的证据")
        now = to_storage(self.clock.now())
        binding = self.bindings.create(
            package_id=package_id, evidence_id=evidence["id"],
            subject_kind=data["subject_kind"], subject_ref=data["subject_ref"],
            interpretation=data["interpretation"], note=data.get("note", ""),
            actor_user_id=principal.user_id, now=now,
        )
        self.packages.add_event(package_id, "binding.created", principal.user_id,
                                f"复用证据 {evidence['source_ref']} 到 {data['subject_kind']}:{data['subject_ref']}",
                                {"binding_id": binding["id"], "evidence_id": evidence["id"]}, now)
        self.audit.record(principal, "evidence_binding.create", "evidence_binding", str(binding["id"]), after=binding)
        return binding

    def update_binding(self, principal: Principal, binding_id: int, data: dict[str, Any]) -> dict[str, Any]:
        principal.require("evidence.write")
        before = self.bindings.get(binding_id)
        now = to_storage(self.clock.now())
        updated = self.bindings.update(binding_id, data["interpretation"], data.get("note", ""), now)
        self.packages.add_event(before["package_id"], "binding.updated", principal.user_id,
                                f"更新复用解释（绑定 {binding_id}）", {"binding_id": binding_id}, now)
        self.audit.record(principal, "evidence_binding.update", "evidence_binding", str(binding_id),
                          before=before, after=updated)
        return updated

    def list_bindings_by_subject(self, principal: Principal, subject_kind: str, subject_ref: str) -> list[dict[str, Any]]:
        principal.require("evidence.read")
        bindings = self.bindings.list_by_subject(subject_kind, subject_ref)
        rows = []
        for binding in bindings:
            item = self.items.get(binding["evidence_id"])
            binding["evidence"] = {k: item[k] for k in ("source_kind", "source_ref", "title", "published_at", "content_digest")}
            rows.append(binding)
        return rows
