from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.archives.repository import row_dict


class EvidenceRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    # 证据节点：全局去重，只写不改
    def create_node(self, data: dict[str, Any], actor_user_id: int, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO evidence_nodes(evidence_code,source_kind,source_reference,content,content_digest,captured_at,imported_by,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                data["evidence_code"], data["source_kind"], data["source_reference"], data["content"],
                data["content_digest"], data["captured_at"], actor_user_id, now,
            ),
        )
        return self.get_node(cursor.lastrowid)

    def get_node(self, node_id: int) -> dict[str, Any]:
        return row_dict(self.connection.execute("SELECT * FROM evidence_nodes WHERE id=?", (node_id,)).fetchone())

    def node_by_digest(self, content_digest: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM evidence_nodes WHERE content_digest=?", (content_digest,)).fetchone()
        return dict(row) if row else None

    def node_references(self, node_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT e.id AS entry_id,e.position,e.citation_scope,e.interpretation,e.appended_at,
                      p.id AS package_id,p.package_code,p.title,p.subject_kind,p.subject_reference,p.state AS package_state
               FROM evidence_package_entries e JOIN evidence_packages p ON p.id=e.package_id
               WHERE e.evidence_id=? ORDER BY p.id,e.position""",
            (node_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # 证据包
    def create_package(self, data: dict[str, Any], actor_user_id: int, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO evidence_packages(package_code,title,subject_kind,subject_reference,state,created_by,created_at,updated_at)
               VALUES(?,?,?,?,'draft',?,?,?)""",
            (data["package_code"], data["title"], data["subject_kind"], data["subject_reference"], actor_user_id, now, now),
        )
        return self.get_package(cursor.lastrowid)

    def get_package(self, package_id: int) -> dict[str, Any]:
        return row_dict(self.connection.execute("SELECT * FROM evidence_packages WHERE id=?", (package_id,)).fetchone())

    def package_by_code(self, package_code: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM evidence_packages WHERE package_code=?", (package_code,)).fetchone()
        return dict(row) if row else None

    def list_packages(
        self,
        *,
        subject_kind: str | None = None,
        subject_reference: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_kind:
            clauses.append("subject_kind=?")
            params.append(subject_kind)
        if subject_reference:
            clauses.append("subject_reference=?")
            params.append(subject_reference)
        if state:
            clauses.append("state=?")
            params.append(state)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.connection.execute(
            "SELECT * FROM evidence_packages" + where + " ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_submitted(self, package_id: int, now: str) -> dict[str, Any]:
        self.connection.execute(
            "UPDATE evidence_packages SET state='submitted',submitted_at=?,version=version+1,updated_at=? WHERE id=?",
            (now, now, package_id),
        )
        return self.get_package(package_id)

    def mark_archived(self, package_id: int, now: str) -> dict[str, Any]:
        self.connection.execute(
            "UPDATE evidence_packages SET state='archived',archived_at=?,version=version+1,updated_at=? WHERE id=?",
            (now, now, package_id),
        )
        return self.get_package(package_id)

    # 包内条目：只追加，不更新不删除
    def append_entry(self, data: dict[str, Any], actor_user_id: int, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO evidence_package_entries(
                   package_id,evidence_id,position,citation_scope,interpretation,
                   content_digest,prev_entry_digest,entry_digest,import_key,appended_by,appended_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["package_id"], data["evidence_id"], data["position"], data["citation_scope"],
                data["interpretation"], data["content_digest"], data["prev_entry_digest"],
                data["entry_digest"], data["import_key"], actor_user_id, now,
            ),
        )
        return self.get_entry(cursor.lastrowid)

    def get_entry(self, entry_id: int) -> dict[str, Any]:
        return row_dict(self.connection.execute("SELECT * FROM evidence_package_entries WHERE id=?", (entry_id,)).fetchone())

    def entry_by_import_key(self, package_id: int, import_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM evidence_package_entries WHERE package_id=? AND import_key=?",
            (package_id, import_key),
        ).fetchone()
        return dict(row) if row else None

    def last_entry(self, package_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM evidence_package_entries WHERE package_id=? ORDER BY position DESC LIMIT 1",
            (package_id,),
        ).fetchone()
        return dict(row) if row else None

    def entries(self, package_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT e.*,n.evidence_code,n.source_kind,n.source_reference,n.captured_at
               FROM evidence_package_entries e JOIN evidence_nodes n ON n.id=e.evidence_id
               WHERE e.package_id=? ORDER BY e.position""",
            (package_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def entry_count(self, package_id: int) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM evidence_package_entries WHERE package_id=?", (package_id,)
            ).fetchone()[0]
        )

    # 异议与裁决
    def create_objection(self, data: dict[str, Any], actor_user_id: int, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO evidence_objections(objection_code,package_id,entry_id,reason,raised_by,raised_at,state)
               VALUES(?,?,?,?,?,?,'pending')""",
            (data["objection_code"], data["package_id"], data["entry_id"], data["reason"], actor_user_id, now),
        )
        return self.get_objection(cursor.lastrowid)

    def get_objection(self, objection_id: int) -> dict[str, Any]:
        return row_dict(self.connection.execute("SELECT * FROM evidence_objections WHERE id=?", (objection_id,)).fetchone())

    def objection_by_code(self, objection_code: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM evidence_objections WHERE objection_code=?", (objection_code,)).fetchone()
        return dict(row) if row else None

    def decide_objection(self, objection_id: int, outcome: str, resolution: str, actor_user_id: int, now: str) -> dict[str, Any]:
        self.connection.execute(
            """UPDATE evidence_objections SET state=?,resolution=?,decided_by=?,decided_at=?
               WHERE id=? AND state='pending'""",
            (outcome, resolution, actor_user_id, now, objection_id),
        )
        return self.get_objection(objection_id)

    def objections_for_package(self, package_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM evidence_objections WHERE package_id=? ORDER BY id",
            (package_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def pending_objection_count(self, package_id: int) -> int:
        return int(
            self.connection.execute(
                "SELECT COUNT(*) FROM evidence_objections WHERE package_id=? AND state='pending'",
                (package_id,),
            ).fetchone()[0]
        )

    # 生命周期事件：提交、异议、裁决、归档全程留痕
    def append_event(self, package_id: int, event_type: str, actor_user_id: int | None, now: str, details: dict[str, Any] | None = None) -> None:
        self.connection.execute(
            """INSERT INTO evidence_package_events(package_id,event_type,actor_user_id,details_json,occurred_at)
               VALUES(?,?,?,?,?)""",
            (package_id, event_type, actor_user_id, json.dumps(details or {}, ensure_ascii=False, sort_keys=True), now),
        )

    def events(self, package_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM evidence_package_events WHERE package_id=? ORDER BY id",
            (package_id,),
        ).fetchall()
        return [dict(row) for row in rows]
