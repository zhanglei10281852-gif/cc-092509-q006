from __future__ import annotations

import sqlite3
from typing import Any

from app.evidence.chain import entry_chain_digest, evidence_content_digest


def verify_package(connection: sqlite3.Connection, package_id: int) -> dict[str, Any]:
    """重算证据包内每条目的链摘要与证据内容摘要，报告断链、缺位和摘要不匹配。"""
    package = connection.execute("SELECT * FROM evidence_packages WHERE id=?", (package_id,)).fetchone()
    if package is None:
        return {"package_id": package_id, "ok": False, "checked_entries": 0, "problems": [{"kind": "package_missing", "detail": f"证据包 {package_id} 不存在"}]}
    package = dict(package)
    rows = connection.execute(
        """SELECT e.*,n.content AS node_content,n.content_digest AS node_digest,n.source_kind,n.source_reference
           FROM evidence_package_entries e JOIN evidence_nodes n ON n.id=e.evidence_id
           WHERE e.package_id=? ORDER BY e.position""",
        (package_id,),
    ).fetchall()
    problems: list[dict[str, Any]] = []
    previous_digest = ""
    for expected_position, row in enumerate(rows, start=1):
        entry = dict(row)
        label = {"entry_id": entry["id"], "position": entry["position"]}
        if entry["position"] != expected_position:
            problems.append({**label, "kind": "position_gap", "detail": f"条目位置应为 {expected_position}，实际为 {entry['position']}"})
        if entry["prev_entry_digest"] != previous_digest:
            problems.append({**label, "kind": "chain_broken", "detail": "前向链摘要与上一条目不一致，链条断裂"})
        recomputed_entry = entry_chain_digest(
            package_code=package["package_code"],
            position=entry["position"],
            content_digest=entry["content_digest"],
            citation_scope=entry["citation_scope"],
            interpretation=entry["interpretation"],
            prev_entry_digest=entry["prev_entry_digest"],
            appended_at=entry["appended_at"],
        )
        if recomputed_entry != entry["entry_digest"]:
            problems.append({**label, "kind": "entry_digest_mismatch", "detail": "条目链摘要重算结果与存档值不一致"})
        if entry["content_digest"] != entry["node_digest"]:
            problems.append({**label, "kind": "snapshot_mismatch", "detail": "条目保存的内容摘要快照与证据节点当前摘要不一致"})
        recomputed_node = evidence_content_digest(entry["source_kind"], entry["source_reference"], entry["node_content"])
        if recomputed_node != entry["node_digest"]:
            problems.append({**label, "kind": "evidence_digest_mismatch", "detail": "证据正文重算摘要与存档摘要不一致，原文可能已被改动"})
        previous_digest = entry["entry_digest"]
    return {
        "package_id": package_id,
        "package_code": package["package_code"],
        "state": package["state"],
        "checked_entries": len(rows),
        "ok": not problems,
        "problems": problems,
    }


def verify_all_packages(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT id FROM evidence_packages ORDER BY id").fetchall()
    return [verify_package(connection, int(row["id"])) for row in rows]
