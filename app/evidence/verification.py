"""证据链完整性校验。

HTTP 校验接口与 CLI `verify-evidence` 共用同一实现，确保两处对断链、
序号断档、哈希链断裂、摘要不匹配等问题给出一致结论。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.evidence.chain import archive_digest, digest_content, genesis_hash, hash_entry


@dataclass(slots=True)
class Finding:
    code: str
    severity: str  # error / warning
    message: str
    package_id: int | None = None
    package_code: str | None = None
    entry_id: int | None = None
    challenge_id: int | None = None
    evidence_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "package_id": self.package_id,
            "package_code": self.package_code,
            "entry_id": self.entry_id,
            "challenge_id": self.challenge_id,
            "evidence_id": self.evidence_id,
        }


@dataclass(slots=True)
class VerificationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [item for item in self.findings if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self, *, package_count: int, entry_count: int, challenge_count: int, binding_count: int) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": {
                "packages": package_count,
                "entries": entry_count,
                "challenges": challenge_count,
                "bindings": binding_count,
                "errors": len(self.errors),
                "warnings": len(self.warnings),
            },
            "findings": [item.as_dict() for item in self.findings],
        }


def verify_all(connection: sqlite3.Connection) -> tuple[VerificationReport, dict[str, int]]:
    report = VerificationReport()

    evidence_rows = {
        row["id"]: dict(row)
        for row in connection.execute("SELECT * FROM evidence_items ORDER BY id").fetchall()
    }
    packages = {
        row["id"]: dict(row)
        for row in connection.execute("SELECT * FROM evidence_packages ORDER BY id").fetchall()
    }
    entries = [dict(row) for row in connection.execute("SELECT * FROM package_entries ORDER BY package_id, seq").fetchall()]
    challenges = [dict(row) for row in connection.execute("SELECT * FROM evidence_challenges ORDER BY id").fetchall()]
    bindings = [dict(row) for row in connection.execute("SELECT * FROM evidence_bindings ORDER BY id").fetchall()]

    # 1) 证据节点：摘要必须与原始内容一致（检测原文被替换）。
    evidence_recomputed: dict[int, str] = {}
    for evidence_id, item in evidence_rows.items():
        recomputed = digest_content(json.loads(item["content_json"]))
        evidence_recomputed[evidence_id] = recomputed
        if item["content_digest"] != recomputed:
            report.findings.append(
                Finding(
                    code="evidence_content_tampered",
                    severity="error",
                    message=f"证据 {item['source_kind']}:{item['source_ref']} 的内容摘要与原始内容不匹配，原文可能被替换",
                    evidence_id=evidence_id,
                )
            )

    # 2) 复用绑定的外键完整性。
    for binding in bindings:
        if binding["evidence_id"] not in evidence_rows:
            report.findings.append(
                Finding("binding_evidence_missing", "error", "复用绑定指向的证据节点不存在", evidence_id=binding["evidence_id"])
            )
        if binding["package_id"] not in packages:
            report.findings.append(
                Finding("binding_package_missing", "error", "复用绑定指向的证据包不存在", package_id=binding["package_id"])
            )

    # 3) 逐包校验哈希链、序号与引用。
    entries_by_package: dict[int, list[dict[str, Any]]] = {}
    for entry in entries:
        entries_by_package.setdefault(entry["package_id"], []).append(entry)

    for package_id, package in packages.items():
        package_entries = entries_by_package.get(package_id, [])
        expected_prev = genesis_hash(package["package_code"])
        for position, entry in enumerate(package_entries, start=1):
            prefix = {"package_id": package_id, "package_code": package["package_code"], "entry_id": entry["id"]}

            if entry["seq"] != position:
                report.findings.append(
                    Finding("entry_seq_gap", "error",
                            f"条目序号断档或乱序：链上第 {position} 条的序号为 {entry['seq']}", **prefix)
                )

            evidence = evidence_rows.get(entry["evidence_id"])
            if evidence is None:
                report.findings.append(
                    Finding("entry_evidence_missing", "error", "条目引用的证据节点不存在，证据链断链", **prefix)
                )
            elif entry["content_digest"] != evidence_recomputed.get(entry["evidence_id"]):
                report.findings.append(
                    Finding("entry_summary_mismatch", "error",
                            "条目的内容摘要与证据节点当前内容不一致，引用的不是登记时看到的内容（原文可能被替换）", **prefix)
                )
            elif entry["content_digest"] != evidence["content_digest"]:
                report.findings.append(
                    Finding("entry_digest_version_mismatch", "error",
                            "条目的入链摘要与证据节点登记摘要不一致", **prefix)
                )

            if entry["prev_hash"] != expected_prev:
                report.findings.append(
                    Finding("chain_broken", "error",
                            f"第 {position} 条的前序哈希与上一条不匹配，哈希链断裂", **prefix)
                )

            recomputed_hash = hash_entry(
                package_code=package["package_code"],
                seq=entry["seq"],
                entry_kind=entry["entry_kind"],
                evidence_id=entry["evidence_id"],
                content_digest=entry["content_digest"],
                quote_range=entry["quote_range"],
                summary=entry["summary"],
                note=entry["note"],
                appended_by=entry["appended_by"],
                created_at=entry["created_at"],
                prev_hash=entry["prev_hash"],
            )
            if recomputed_hash != entry["entry_hash"]:
                report.findings.append(
                    Finding("entry_hash_mismatch", "error",
                            "条目哈希重算结果与入链哈希不一致，条目内容可能被篡改", **prefix)
                )
            expected_prev = entry["entry_hash"]

        head = package_entries[-1]["entry_hash"] if package_entries else None
        if not package_entries and package["head_digest"]:
            report.findings.append(
                Finding("chain_truncated", "error",
                        "证据包记录了链头摘要但链上没有任何条目，哈希链被截断",
                        package_id=package_id, package_code=package["package_code"])
            )
        elif package_entries and package["head_digest"] != head:
            report.findings.append(
                Finding("package_head_mismatch", "error",
                        "证据包链头摘要与最后一条不一致，链可能被截断或改写",
                        package_id=package_id, package_code=package["package_code"])
            )

        if package["state"] == "archived":
            if not package["archive_digest"]:
                report.findings.append(
                    Finding("archive_digest_missing", "error", "已归档证据包缺少归档摘要",
                            package_id=package_id, package_code=package["package_code"])
                )
            else:
                expected_archive = archive_digest(package["package_code"], len(package_entries), head)
                if expected_archive != package["archive_digest"]:
                    report.findings.append(
                        Finding("archive_digest_mismatch", "error",
                                "归档摘要与当前链不一致，归档后链内容发生过变化",
                                package_id=package_id, package_code=package["package_code"])
                    )

    # 4) 异议与裁决引用完整性。
    for challenge in challenges:
        package = packages.get(challenge["package_id"])
        prefix = {"package_id": challenge["package_id"],
                  "package_code": package["package_code"] if package else None,
                  "challenge_id": challenge["id"]}
        if package is None:
            report.findings.append(Finding("challenge_package_missing", "error", "异议指向的证据包不存在", **prefix))
            continue

        if challenge["entry_id"] is not None:
            target = next((entry for entry in entries_by_package.get(challenge["package_id"], [])
                           if entry["id"] == challenge["entry_id"]), None)
            if target is None:
                report.findings.append(
                    Finding("challenge_entry_missing", "error", "异议指向的条目不存在或不属于该证据包，引用断链", **prefix)
                )

        if challenge["state"] != "open":
            if not challenge["decided_by"] or not challenge["decided_at"] or not challenge["decision_note"]:
                report.findings.append(
                    Finding("challenge_decision_incomplete", "error", "已裁决异议缺少裁决人、裁决时间或裁决意见", **prefix)
                )

        if challenge["resolution_entry_id"] is not None:
            resolution = next((entry for entry in entries_by_package.get(challenge["package_id"], [])
                               if entry["id"] == challenge["resolution_entry_id"]), None)
            if resolution is None:
                report.findings.append(
                    Finding("resolution_entry_missing", "error", "裁决补正条目不存在或不属于该证据包，引用断链", **prefix)
                )
            elif challenge["state"] == "amended" and resolution["entry_kind"] != "supplement":
                report.findings.append(
                    Finding("resolution_entry_wrong_kind", "error", "补正裁决必须引用补充条目", **prefix)
                )

        if challenge["state"] == "open" and package["state"] == "archived":
            report.findings.append(
                Finding("open_challenge_in_archived", "warning", "已归档证据包仍存在未裁决异议", **prefix)
            )

    counts = {
        "package_count": len(packages),
        "entry_count": len(entries),
        "challenge_count": len(challenges),
        "binding_count": len(bindings),
    }
    return report, counts


def build_report(connection: sqlite3.Connection) -> dict[str, Any]:
    report, counts = verify_all(connection)
    return report.as_dict(
        package_count=counts["package_count"],
        entry_count=counts["entry_count"],
        challenge_count=counts["challenge_count"],
        binding_count=counts["binding_count"],
    )
