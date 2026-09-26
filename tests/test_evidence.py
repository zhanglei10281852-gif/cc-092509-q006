from __future__ import annotations

import json
import os
import subprocess
import sys


def _make_evidence(client, headers, *, source_ref="10.1000/novel-001", key=None, content=None):
    payload = {
        "source_kind": "publication",
        "source_ref": source_ref,
        "title": "现有技术公开文献",
        "published_at": "2024-05-01T00:00:00+00:00",
        "content": content if content is not None else {"abstract": "一种结构", "claims": ["权1"]},
    }
    if key:
        payload["idempotency_key"] = key
    response = client.post("/api/evidence/items", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _make_package(client, headers, *, code="EVK-001", subject_ref="DIS-2026-001"):
    response = client.post(
        "/api/evidence/packages",
        headers=headers,
        json={"package_code": code, "subject_kind": "disclosure", "subject_ref": subject_ref,
              "title": "交底新颖性证据包"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _append_entry(client, headers, package_id, evidence_id, *, summary="该文献第3页公开了相同结构",
                  quote_range="第3页第2段", kind="primary", key=None):
    payload = {"evidence_id": evidence_id, "summary": summary, "quote_range": quote_range, "entry_kind": kind}
    if key:
        payload["idempotency_key"] = key
    response = client.post(f"/api/evidence/packages/{package_id}/entries", headers=headers, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_import_dedup_same_content_no_duplicate_node(client, admin):
    first = _make_evidence(client, admin["headers"], key="IMP-001")
    second = _make_evidence(client, admin["headers"], key="IMP-001")
    assert first["item"]["id"] == second["item"]["id"]
    assert first["replayed"] is False and second["replayed"] is True

    # 自然键（来源类型+来源标识）相同也只返回既有节点。
    third = _make_evidence(client, admin["headers"])
    assert third["item"]["id"] == first["item"]["id"]
    assert third["replayed"] is True


def test_reimport_with_changed_content_is_rejected_not_replaced(client, admin):
    original = _make_evidence(client, admin["headers"])
    changed = client.post(
        "/api/evidence/items",
        headers=admin["headers"],
        json={
            "source_kind": "publication",
            "source_ref": "10.1000/novel-001",
            "title": "现有技术公开文献",
            "content": {"abstract": "被篡改后的不同结构"},
        },
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["context"]["evidence_id"] == original["item"]["id"]


def test_entries_form_ordered_hash_chain(client, admin):
    evidence = _make_evidence(client, admin["headers"])
    package = _make_package(client, admin["headers"])
    first = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"], key="ENT-1")
    second = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"],
                           summary="实验日志记录在先使用", quote_range="第12册第8行", kind="supplement")

    assert first["entry"]["seq"] == 1 and second["entry"]["seq"] == 2
    assert second["entry"]["prev_hash"] == first["entry"]["entry_hash"]
    assert first["entry"]["prev_hash"] != first["entry"]["entry_hash"]

    # 幂等重放返回同一条目，不产生新节点。
    replayed = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"], key="ENT-1")
    assert replayed["entry"]["id"] == first["entry"]["id"]
    assert replayed["replayed"] is True


def test_replay_append_with_changed_summary_is_rejected(client, admin):
    evidence = _make_evidence(client, admin["headers"])
    package = _make_package(client, admin["headers"])
    _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"], key="ENT-LOCK")
    changed = client.post(
        f"/api/evidence/packages/{package['id']}/entries",
        headers=admin["headers"],
        json={"evidence_id": evidence["item"]["id"], "summary": "重放时偷换的摘要内容",
              "quote_range": "第3页第2段", "idempotency_key": "ENT-LOCK"},
    )
    assert changed.status_code == 422


def test_submit_requires_entries_and_archive_requires_resolved_challenges(client, admin):
    package = _make_package(client, admin["headers"])
    empty_submit = client.post(f"/api/evidence/packages/{package['id']}/submit", headers=admin["headers"])
    assert empty_submit.status_code == 422

    evidence = _make_evidence(client, admin["headers"])
    _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"])
    submitted = client.post(f"/api/evidence/packages/{package['id']}/submit", headers=admin["headers"])
    assert submitted.status_code == 200
    assert submitted.json()["package"]["state"] == "submitted"

    # 提交后仍可追加补充材料。
    supplemented = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"],
                                 summary="后补的检索意见说明", quote_range="意见正文第2节", kind="supplement")
    assert supplemented["entry"]["seq"] == 2

    # 存在未裁决异议时不能归档。
    challenge = client.post(
        f"/api/evidence/packages/{package['id']}/challenges",
        headers=admin["headers"],
        json={"entry_id": supplemented["entry"]["id"], "reason": "补充材料的引用范围超出原文"},
    )
    assert challenge.status_code == 201
    blocked = client.post(f"/api/evidence/packages/{package['id']}/archive", headers=admin["headers"])
    assert blocked.status_code == 409


def test_challenge_decision_requires_separation_of_duty(client, admin):
    decision_maker = _login_decider(client, admin)
    evidence = _make_evidence(client, admin["headers"])
    package = _make_package(client, admin["headers"], code="EVK-DUTY")
    entry = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"])
    client.post(f"/api/evidence/packages/{package['id']}/submit", headers=admin["headers"])

    challenge = client.post(
        f"/api/evidence/packages/{package['id']}/challenges",
        headers=admin["headers"],
        json={"entry_id": entry["entry"]["id"], "reason": "摘要与原文不符"},
    ).json()

    # 提出人自己裁决被拒绝。
    self_decide = client.post(
        f"/api/evidence/challenges/{challenge['challenge']['id']}/decision",
        headers=admin["headers"],
        json={"outcome": "rejected", "decision_note": "自己驳回不允许"},
    )
    assert self_decide.status_code == 422

    # 另一裁决人驳回，原条目保持有效。
    decided = client.post(
        f"/api/evidence/challenges/{challenge['challenge']['id']}/decision",
        headers=decision_maker,
        json={"outcome": "rejected", "decision_note": "摘要准确，异议驳回"},
    )
    assert decided.status_code == 200
    assert decided.json()["state"] == "rejected"
    assert decided.json()["decided_by"] != challenge["challenge"]["raised_by"]

    # 已裁决异议不能二次裁决。
    again = client.post(
        f"/api/evidence/challenges/{challenge['challenge']['id']}/decision",
        headers=decision_maker,
        json={"outcome": "upheld", "decision_note": "重复裁决"},
    )
    assert again.status_code == 409


def test_amend_decision_must_reference_matching_supplement(client, admin):
    decision_maker = _login_decider(client, admin)
    evidence = _make_evidence(client, admin["headers"], source_ref="10.1000/lab-9",
                              content={"log": "实验记录内容"})
    package = _make_package(client, admin["headers"], code="EVK-AMD")
    entry = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"])
    challenge = client.post(
        f"/api/evidence/packages/{package['id']}/challenges",
        headers=admin["headers"],
        json={"entry_id": entry["entry"]["id"], "reason": "引用页码错误"},
    ).json()

    # 补正裁决缺少补充条目。
    missing = client.post(
        f"/api/evidence/challenges/{challenge['challenge']['id']}/decision",
        headers=decision_maker,
        json={"outcome": "amended", "decision_note": "缺补充条目"},
    )
    assert missing.status_code == 422

    supplement = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"],
                               summary="更正页码后的内容摘要", quote_range="第4页第1段", kind="supplement")
    amended = client.post(
        f"/api/evidence/challenges/{challenge['challenge']['id']}/decision",
        headers=decision_maker,
        json={"outcome": "amended", "decision_note": "以补充条目更正引用页码",
              "resolution_entry_id": supplement["entry"]["id"]},
    )
    assert amended.status_code == 200
    assert amended.json()["state"] == "amended"
    assert amended.json()["resolution_entry_id"] == supplement["entry"]["id"]


def test_evidence_reused_across_subjects_keeps_separate_interpretations(client, admin):
    evidence = _make_evidence(client, admin["headers"])
    package_one = _make_package(client, admin["headers"], code="EVK-FAM-A", subject_ref="DIS-A")
    entry = _append_entry(client, admin["headers"], package_one["id"], evidence["item"]["id"])

    # 另一个交底/专利家族复用同一证据，但解释各自独立。
    binding = client.post(
        f"/api/evidence/packages/{package_one['id']}/bindings",
        headers=admin["headers"],
        json={"evidence_id": evidence["item"]["id"], "subject_kind": "patent_family",
              "subject_ref": "FAM-2026-EU", "interpretation": "在欧洲家族中用于评述权2创造性"},
    )
    assert binding.status_code == 201, binding.text
    binding_id = binding.json()["id"]

    other = client.post(
        f"/api/evidence/packages/{package_one['id']}/bindings",
        headers=admin["headers"],
        json={"evidence_id": evidence["item"]["id"], "subject_kind": "disclosure",
              "subject_ref": "DIS-B", "interpretation": "在另一交底中仅作为背景技术"},
    )
    assert other.status_code == 201

    # 同一证据重复绑定到同一对象不产生重复绑定。
    duplicate = client.post(
        f"/api/evidence/packages/{package_one['id']}/bindings",
        headers=admin["headers"],
        json={"evidence_id": evidence["item"]["id"], "subject_kind": "patent_family",
              "subject_ref": "FAM-2026-EU", "interpretation": "重复绑定"},
    )
    assert duplicate.status_code == 409

    updated = client.patch(
        f"/api/evidence/bindings/{binding_id}",
        headers=admin["headers"],
        json={"interpretation": "更新后的欧洲家族解释，不影响其他交底的解释"},
    )
    assert updated.status_code == 200

    listing = client.get(
        "/api/evidence/bindings",
        headers=admin["headers"],
        params={"subject_kind": "patent_family", "subject_ref": "FAM-2026-EU"},
    )
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["evidence"]["source_ref"] == evidence["item"]["source_ref"]
    assert rows[0]["interpretation"].startswith("更新后")

    # 复用的证据仍是同一个全局节点。
    assert entry["entry"]["evidence_id"] == evidence["item"]["id"]


def test_full_lifecycle_submit_decide_archive_is_verifiable(client, admin):
    decision_maker = _login_decider(client, admin)
    evidence = _make_evidence(client, admin["headers"], source_ref="OPN-S-1")
    package = _make_package(client, admin["headers"], code="EVK-LIFE")
    _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"])
    client.post(f"/api/evidence/packages/{package['id']}/submit", headers=admin["headers"])

    challenge = client.post(
        f"/api/evidence/packages/{package['id']}/challenges",
        headers=admin["headers"],
        json={"reason": "证据包整体缺少实验日志来源"},
    ).json()
    client.post(
        f"/api/evidence/challenges/{challenge['challenge']['id']}/decision",
        headers=decision_maker,
        json={"outcome": "upheld", "decision_note": "异议成立，需补充实验日志"},
    )

    archived = client.post(f"/api/evidence/packages/{package['id']}/archive", headers=admin["headers"])
    assert archived.status_code == 200
    assert archived.json()["package"]["state"] == "archived"
    assert archived.json()["package"]["archive_digest"]

    # 归档后不能追加或提异议。
    more = client.post(
        f"/api/evidence/packages/{package['id']}/entries",
        headers=admin["headers"],
        json={"evidence_id": evidence["item"]["id"], "summary": "归档后追加应当被拒绝的内容摘要",
              "quote_range": "第1页"},
    )
    assert more.status_code == 409
    objection = client.post(
        f"/api/evidence/packages/{package['id']}/challenges",
        headers=admin["headers"],
        json={"reason": "归档后异议"},
    )
    assert objection.status_code == 409

    # 详情含有序链、异议处理结果与完整事件流。
    detail = client.get(f"/api/evidence/packages/{package['id']}", headers=admin["headers"]).json()
    assert [entry["seq"] for entry in detail["entries"]] == [1]
    assert detail["challenges"][0]["state"] == "upheld"
    event_types = [event["event_type"] for event in detail["events"]]
    assert "package.created" in event_types
    assert "package.submitted" in event_types
    assert "challenge.raised" in event_types
    assert "challenge.decided" in event_types
    assert "package.archived" in event_types

    report = client.get("/api/evidence/verification", headers=admin["headers"])
    assert report.status_code == 200
    assert report.json()["ok"] is True


def test_verification_reports_tampered_summary_and_broken_chain(client, admin):
    evidence = _make_evidence(client, admin["headers"], source_ref="TAMPER-1")
    package = _make_package(client, admin["headers"], code="EVK-TAMPER")
    entry = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"])

    import sqlite3

    db_path = os.environ["ARCHIVE_DATABASE_PATH"]
    # 直接改库模拟条目摘要被替换：哈希重算应失配。
    with sqlite3.connect(db_path) as direct:
        direct.execute("UPDATE package_entries SET summary=? WHERE id=?", ("被替换的摘要文本", entry["entry"]["id"]))
        direct.commit()

    report = client.get("/api/evidence/verification", headers=admin["headers"])
    assert report.status_code == 422
    codes = {finding["code"] for finding in report.json()["findings"]}
    assert "entry_hash_mismatch" in codes

    # 同时模拟原文被替换：证据内容摘要失配。
    with sqlite3.connect(db_path) as direct:
        direct.execute(
            "UPDATE evidence_items SET content_json=? WHERE id=?",
            (json.dumps({"abstract": "另一份内容"}, ensure_ascii=False), evidence["item"]["id"]),
        )
        direct.commit()
    report = client.get("/api/evidence/verification", headers=admin["headers"])
    codes = {finding["code"] for finding in report.json()["findings"]}
    assert "evidence_content_tampered" in codes
    assert "entry_summary_mismatch" in codes

    # 离线 CLI 校验同样明确报告并以非零码退出。
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", "verify-evidence"],
        env={**os.environ},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "evidence_content_tampered" in result.stdout


def test_verification_reports_broken_link_when_middle_entry_removed(client, admin):
    evidence = _make_evidence(client, admin["headers"], source_ref="CHAIN-BRK")
    package = _make_package(client, admin["headers"], code="EVK-BROKEN")
    first = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"])
    second = _append_entry(client, admin["headers"], package["id"], evidence["item"]["id"],
                           summary="第二条用于制造中间断链", quote_range="第9页")
    assert second["entry"]["seq"] == 2

    import sqlite3

    db_path = os.environ["ARCHIVE_DATABASE_PATH"]
    with sqlite3.connect(db_path) as direct:
        direct.execute("PRAGMA foreign_keys=OFF")
        direct.execute("DELETE FROM package_entries WHERE id=?", (first["entry"]["id"],))
        direct.commit()

    report = client.get("/api/evidence/verification", headers=admin["headers"])
    assert report.status_code == 422
    codes = {finding["code"] for finding in report.json()["findings"]}
    assert {"entry_seq_gap", "chain_broken"} <= codes
    assert "package_head_mismatch" not in codes


def _login_decider(client, admin):
    created = client.post(
        "/api/users",
        headers=admin["headers"],
        json={"username": "decider", "password": "Decide!23456", "display_name": "异议裁决人",
              "role_codes": ["approver"]},
    )
    assert created.status_code == 201, created.text
    login = client.post(
        "/api/auth/login",
        json={"username": "decider", "password": "Decide!23456", "client_label": "tests"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}
