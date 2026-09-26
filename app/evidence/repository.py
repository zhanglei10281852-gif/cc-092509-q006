from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.errors import ConflictError, NotFoundError


def _row(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        raise NotFoundError("记录不存在")
    return dict(row)


class EvidenceItemRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, data: dict[str, Any], digest: str, actor_user_id: int, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO evidence_items(source_kind,source_ref,title,published_at,content_json,content_digest,
               idempotency_key,imported_by,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                data["source_kind"], data["source_ref"], data["title"], data.get("published_at"),
                json.dumps(data["content"], ensure_ascii=False), digest, data.get("idempotency_key"),
                actor_user_id, now, now,
            ),
        )
        return self.get(cursor.lastrowid)

    def get(self, evidence_id: int) -> dict[str, Any]:
        row = _row(self.connection.execute("SELECT * FROM evidence_items WHERE id=?", (evidence_id,)).fetchone())
        row["content"] = json.loads(row.pop("content_json"))
        return row

    def by_natural_key(self, source_kind: str, source_ref: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM evidence_items WHERE source_kind=? AND source_ref=?",
            (source_kind, source_ref),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json"))
        return item

    def by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM evidence_items WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["content"] = json.loads(item.pop("content_json"))
        return item

    def list(self, *, source_kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if source_kind:
            rows = self.connection.execute(
                "SELECT * FROM evidence_items WHERE source_kind=? ORDER BY id DESC LIMIT ?",
                (source_kind, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM evidence_items ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item.pop("content_json", None)
            result.append(item)
        return result


class EvidencePackageRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, data: dict[str, Any], now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO evidence_packages(package_code,subject_kind,subject_ref,title,note,state,created_at,updated_at)
               VALUES(?,?,?,?,?,'open',?,?)""",
            (data["package_code"], data["subject_kind"], data["subject_ref"], data["title"], data.get("note", ""), now, now),
        )
        return self.get(cursor.lastrowid)

    def get(self, package_id: int) -> dict[str, Any]:
        return _row(self.connection.execute("SELECT * FROM evidence_packages WHERE id=?", (package_id,)).fetchone())

    def by_code(self, package_code: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM evidence_packages WHERE package_code=?", (package_code,)).fetchone()
        return dict(row) if row else None

    def list(self, *, subject_kind: str | None = None, subject_ref: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject_kind:
            clauses.append("subject_kind=?")
            params.append(subject_kind)
        if subject_ref:
            clauses.append("subject_ref=?")
            params.append(subject_ref)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.connection.execute(
            "SELECT * FROM evidence_packages" + where + " ORDER BY id DESC LIMIT ?", tuple(params)
        ).fetchall()
        return [dict(row) for row in rows]

    def all_ids(self) -> list[int]:
        return [row[0] for row in self.connection.execute("SELECT id FROM evidence_packages ORDER BY id").fetchall()]

    def next_seq(self, package_id: int) -> int:
        return int(self.connection.execute(
            "SELECT COALESCE(MAX(seq),0)+1 FROM package_entries WHERE package_id=?", (package_id,)
        ).fetchone()[0])

    def append_entry(
        self,
        *,
        package_id: int,
        evidence_id: int,
        seq: int,
        entry_kind: str,
        quote_range: str,
        summary: str,
        content_digest: str,
        note: str,
        idempotency_key: str | None,
        appended_by: int,
        prev_hash: str,
        entry_hash: str,
        now: str,
    ) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO package_entries(package_id,evidence_id,seq,entry_kind,quote_range,summary,content_digest,
               note,idempotency_key,appended_by,prev_hash,entry_hash,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                package_id, evidence_id, seq, entry_kind, quote_range, summary, content_digest,
                note, idempotency_key, appended_by, prev_hash, entry_hash, now,
            ),
        )
        self.connection.execute(
            "UPDATE evidence_packages SET head_digest=?,version=version+1,updated_at=? WHERE id=?",
            (entry_hash, now, package_id),
        )
        return self.get_entry(cursor.lastrowid)

    def get_entry(self, entry_id: int) -> dict[str, Any]:
        return _row(self.connection.execute("SELECT * FROM package_entries WHERE id=?", (entry_id,)).fetchone())

    def find_entry_by_idempotency_key(self, package_id: int, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM package_entries WHERE package_id=? AND idempotency_key=?", (package_id, key)
        ).fetchone()
        return dict(row) if row else None

    def entries(self, package_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM package_entries WHERE package_id=? ORDER BY seq", (package_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def all_entries(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM package_entries ORDER BY package_id, seq"
        ).fetchall()]

    def mark_submitted(self, package_id: int, actor_user_id: int, now: str) -> dict[str, Any]:
        self.connection.execute(
            "UPDATE evidence_packages SET state='submitted',submitted_at=?,submitted_by=?,updated_at=? WHERE id=?",
            (now, actor_user_id, now, package_id),
        )
        return self.get(package_id)

    def mark_archived(self, package_id: int, actor_user_id: int, archive_digest: str, now: str) -> dict[str, Any]:
        self.connection.execute(
            """UPDATE evidence_packages SET state='archived',archived_at=?,archived_by=?,archive_digest=?,
               updated_at=? WHERE id=?""",
            (now, actor_user_id, archive_digest, now, package_id),
        )
        return self.get(package_id)

    def add_event(self, package_id: int, event_type: str, actor_user_id: int, summary: str, details: dict[str, Any], now: str) -> None:
        self.connection.execute(
            "INSERT INTO package_events(package_id,event_type,actor_user_id,summary,details_json,created_at) VALUES(?,?,?,?,?,?)",
            (package_id, event_type, actor_user_id, summary, json.dumps(details or {}, ensure_ascii=False), now),
        )

    def events(self, package_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM package_events WHERE package_id=? ORDER BY id", (package_id,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result


class ChallengeRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, *, package_id: int, entry_id: int | None, reason: str, raised_by: int, idempotency_key: str | None, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            """INSERT INTO evidence_challenges(package_id,entry_id,reason,state,raised_by,raised_at,
               idempotency_key,created_at,updated_at) VALUES(?,?,?, 'open',?,?,?,?,?)""",
            (package_id, entry_id, reason, raised_by, now, idempotency_key, now, now),
        )
        return self.get(cursor.lastrowid)

    def get(self, challenge_id: int) -> dict[str, Any]:
        return _row(self.connection.execute("SELECT * FROM evidence_challenges WHERE id=?", (challenge_id,)).fetchone())

    def find_by_idempotency_key(self, package_id: int, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM evidence_challenges WHERE package_id=? AND idempotency_key=?", (package_id, key)
        ).fetchone()
        return dict(row) if row else None

    def list_for_package(self, package_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM evidence_challenges WHERE package_id=? ORDER BY id", (package_id,)
        ).fetchall()]

    def all(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM evidence_challenges ORDER BY package_id, id"
        ).fetchall()]

    def open_count(self, package_id: int) -> int:
        return int(self.connection.execute(
            "SELECT COUNT(*) FROM evidence_challenges WHERE package_id=? AND state='open'", (package_id,)
        ).fetchone()[0])

    def decide(self, challenge_id: int, *, state: str, decided_by: int, decision_note: str, resolution_entry_id: int | None, now: str) -> dict[str, Any]:
        updated = self.connection.execute(
            """UPDATE evidence_challenges SET state=?,decided_by=?,decided_at=?,decision_note=?,
               resolution_entry_id=?,updated_at=? WHERE id=? AND state='open'""",
            (state, decided_by, now, decision_note, resolution_entry_id, now, challenge_id),
        )
        if updated.rowcount != 1:
            raise ConflictError("异议不存在或已经裁决")
        return self.get(challenge_id)

    def withdraw(self, challenge_id: int, now: str) -> dict[str, Any]:
        updated = self.connection.execute(
            "UPDATE evidence_challenges SET state='withdrawn',updated_at=? WHERE id=? AND state='open'",
            (now, challenge_id),
        )
        if updated.rowcount != 1:
            raise ConflictError("异议不存在或已经结束，无法撤回")
        return self.get(challenge_id)


class BindingRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, *, package_id: int, evidence_id: int, subject_kind: str, subject_ref: str, interpretation: str, note: str, actor_user_id: int, now: str) -> dict[str, Any]:
        try:
            cursor = self.connection.execute(
                """INSERT INTO evidence_bindings(package_id,evidence_id,subject_kind,subject_ref,interpretation,
                   note,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (package_id, evidence_id, subject_kind, subject_ref, interpretation, note, actor_user_id, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("该证据已经绑定到这个交底或专利家族") from exc
        return self.get(cursor.lastrowid)

    def get(self, binding_id: int) -> dict[str, Any]:
        return _row(self.connection.execute("SELECT * FROM evidence_bindings WHERE id=?", (binding_id,)).fetchone())

    def list_for_package(self, package_id: int) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM evidence_bindings WHERE package_id=? ORDER BY id", (package_id,)
        ).fetchall()]

    def list_by_subject(self, subject_kind: str, subject_ref: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM evidence_bindings WHERE subject_kind=? AND subject_ref=? ORDER BY id",
            (subject_kind, subject_ref),
        ).fetchall()]

    def all(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(
            "SELECT * FROM evidence_bindings ORDER BY package_id, id"
        ).fetchall()]

    def update(self, binding_id: int, interpretation: str, note: str, now: str) -> dict[str, Any]:
        self.connection.execute(
            "UPDATE evidence_bindings SET interpretation=?,note=?,updated_at=? WHERE id=?",
            (interpretation, note, now, binding_id),
        )
        return self.get(binding_id)
