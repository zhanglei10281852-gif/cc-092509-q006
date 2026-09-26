from __future__ import annotations

import json


def _create_adjudicator(client, admin):
    role = client.post(
        "/api/roles",
        headers=admin["headers"],
        json={"code": "evidence.judge", "name": "证据裁决人", "permission_codes": ["evidence.read", "evidence.adjudicate"]},
    )
    assert role.status_code == 201, role.text
    user = client.post(
        "/api/users",
        headers=admin["headers"],
        json={"username": "judge.one", "password": "Judge!234567", "display_name": "裁决员甲", "role_codes": ["evidence.judge"]},
    )
    assert user.status_code == 201, user.text
    login = client.post("/api/auth/login", json={"username": "judge.one", "password": "Judge!234567", "client_label": "tests"})
    assert login.status_code == 200, login.text
    return {"headers": {"Authorization": f"Bearer {login.json()['token']}"}}


def _create_package(client, admin, code="PKG-NOV-001", subject_kind="disclosure", subject_reference="DISC-2026-001"):
    response = client.post(
        "/api/evidence/packages",
        headers=admin["headers"],
        json={"package_code": code, "title": "新颖性评估证据包", "subject_kind": subject_kind, "subject_reference": subject_reference},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _append_entry(client, admin, package_id, import_key, source_kind="public_document", content="对比文件正文", reference="CN101XXXXXXA", scope="第 3-5 页", interpretation="公开了权利要求1的特征A"):
    return client.post(
        f"/api/evidence/packages/{package_id}/entries",
        headers=admin["headers"],
        json={
            "import_key": import_key,
            "source_kind": source_kind,
            "source_reference": reference,
            "content": content,
            "captured_at": "2026-09-20T09:30:00+00:00",
            "citation_scope": scope,
            "interpretation": interpretation,
        },
    )


def test_package_lifecycle_chain_and_traceability(client, admin):
    judge = _create_adjudicator(client, admin)
    package = _create_package(client, admin)
    package_id = package["id"]
    assert package["state"] == "draft"

    sources = [
        ("imp-1", "public_document", "对比文件A正文", "CN101AAAAAA"),
        ("imp-2", "experiment_log", "实验记录第42页", "LAB-LOG-2026-042"),
        ("imp-3", "search_opinion", "检索意见：无破坏新颖性文件", "OPINION-2026-09"),
    ]
    digests = []
    for import_key, kind, content, reference in sources:
        response = _append_entry(client, admin, package_id, import_key, source_kind=kind, content=content, reference=reference)
        assert response.status_code == 201, response.text
        digests.append(response.json()["entry"]["entry_digest"])

    chain = client.get(f"/api/evidence/packages/{package_id}/chain", headers=admin["headers"])
    assert chain.status_code == 200
    entries = chain.json()["entries"]
    assert [entry["position"] for entry in entries] == [1, 2, 3]
    assert [entry["source_kind"] for entry in entries] == ["public_document", "experiment_log", "search_opinion"]
    assert entries[0]["prev_entry_digest"] == ""
    assert entries[1]["prev_entry_digest"] == digests[0]
    assert entries[2]["prev_entry_digest"] == digests[1]
    assert all(len(entry["content_digest"]) == 64 for entry in entries)
    assert all(entry["citation_scope"] for entry in entries)

    submitted = client.post(f"/api/evidence/packages/{package_id}/submit", headers=admin["headers"])
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["state"] == "submitted"
    assert submitted.json()["submitted_at"]

    objection = client.post(
        f"/api/evidence/packages/{package_id}/objections",
        headers=admin["headers"],
        json={"entry_id": entries[0]["id"], "reason": "对比文件公开日与申请日关系存疑"},
    )
    assert objection.status_code == 201, objection.text
    objection_id = objection.json()["id"]
    assert objection.json()["state"] == "pending"

    decided = client.post(
        f"/api/evidence/objections/{objection_id}/decide",
        headers=judge["headers"],
        json={"outcome": "dismissed", "resolution": "公开日早于申请日，异议不成立"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["state"] == "dismissed"
    assert decided.json()["resolution"] == "公开日早于申请日，异议不成立"

    archived = client.post(f"/api/evidence/packages/{package_id}/archive", headers=admin["headers"])
    assert archived.status_code == 200, archived.text
    assert archived.json()["state"] == "archived"
    assert archived.json()["archived_at"]

    detail = client.get(f"/api/evidence/packages/{package_id}", headers=admin["headers"])
    event_types = [event["event_type"] for event in detail.json()["events"]]
    assert event_types == [
        "package.created",
        "entry.appended",
        "entry.appended",
        "entry.appended",
        "package.submitted",
        "objection.raised",
        "objection.decided",
        "package.archived",
    ]
    assert detail.json()["objections"][0]["state"] == "dismissed"

    verify = client.get(f"/api/evidence/packages/{package_id}/verify", headers=admin["headers"])
    assert verify.status_code == 200
    assert verify.json()["ok"] is True
    assert verify.json()["checked_entries"] == 3


def test_duplicate_import_never_creates_duplicate_nodes(client, admin):
    package = _create_package(client, admin)
    package_id = package["id"]
    first = _append_entry(client, admin, package_id, "imp-dup-1")
    assert first.status_code == 201
    second = _append_entry(client, admin, package_id, "imp-dup-1")
    assert second.status_code == 201
    assert second.json()["replayed"] is True
    assert second.json()["entry"]["id"] == first.json()["entry"]["id"]

    conflict = _append_entry(client, admin, package_id, "imp-dup-1", content="另一份不同正文")
    assert conflict.status_code == 409

    detail = client.get(f"/api/evidence/packages/{package_id}", headers=admin["headers"])
    assert len(detail.json()["entries"]) == 1


def test_same_evidence_reused_across_subjects_with_own_interpretation(client, admin):
    disclosure_pkg = _create_package(client, admin, code="PKG-DISC-01", subject_kind="disclosure", subject_reference="DISC-001")
    family_pkg = _create_package(client, admin, code="PKG-FAMILY-01", subject_kind="patent_family", subject_reference="FAM-CN-7788")

    first = _append_entry(client, admin, disclosure_pkg["id"], "disc-imp-1", interpretation="用于评述交底书权利要求1")
    assert first.json()["evidence_reused"] is False
    second = _append_entry(client, admin, family_pkg["id"], "fam-imp-1", interpretation="用于评述家族成员CN-B的创造性")
    assert second.json()["evidence_reused"] is True
    assert second.json()["entry"]["evidence_id"] == first.json()["entry"]["evidence_id"]

    node = client.get(f"/api/evidence/nodes/{first.json()['entry']['evidence_id']}", headers=admin["headers"])
    assert node.status_code == 200
    references = node.json()["references"]
    assert len(references) == 2
    by_package = {item["package_code"]: item for item in references}
    assert by_package["PKG-DISC-01"]["interpretation"] == "用于评述交底书权利要求1"
    assert by_package["PKG-FAMILY-01"]["interpretation"] == "用于评述家族成员CN-B的创造性"

    nodes_count = client.get("/api/evidence/packages?subject_kind=patent_family", headers=admin["headers"])
    assert nodes_count.status_code == 200
    assert nodes_count.json()[0]["package_code"] == "PKG-FAMILY-01"


def test_append_only_and_archive_seals_package(client, admin):
    package = _create_package(client, admin)
    package_id = package["id"]
    _append_entry(client, admin, package_id, "imp-seal-1")
    client.post(f"/api/evidence/packages/{package_id}/submit", headers=admin["headers"])

    supplement = _append_entry(client, admin, package_id, "imp-seal-2", content="补充检索到的公开文献", reference="CN102BBBBBB")
    assert supplement.status_code == 201, supplement.text

    archived = client.post(f"/api/evidence/packages/{package_id}/archive", headers=admin["headers"])
    assert archived.status_code == 200

    blocked = _append_entry(client, admin, package_id, "imp-seal-3", content="归档后追加")
    assert blocked.status_code == 409

    replay = _append_entry(client, admin, package_id, "imp-seal-1")
    assert replay.status_code == 201
    assert replay.json()["replayed"] is True


def test_objection_and_archive_rules(client, admin):
    package = _create_package(client, admin)
    package_id = package["id"]
    entry = _append_entry(client, admin, package_id, "imp-rule-1").json()["entry"]

    early_objection = client.post(
        f"/api/evidence/packages/{package_id}/objections",
        headers=admin["headers"],
        json={"entry_id": entry["id"], "reason": "草稿阶段不允许异议"},
    )
    assert early_objection.status_code == 409

    empty_submit = client.post(f"/api/evidence/packages/{_create_package(client, admin, code='PKG-EMPTY')['id']}/submit", headers=admin["headers"])
    assert empty_submit.status_code == 422

    client.post(f"/api/evidence/packages/{package_id}/submit", headers=admin["headers"])
    objection = client.post(
        f"/api/evidence/packages/{package_id}/objections",
        headers=admin["headers"],
        json={"entry_id": entry["id"], "reason": "引用范围与正文不符"},
    )
    assert objection.status_code == 201
    objection_id = objection.json()["id"]

    self_decide = client.post(
        f"/api/evidence/objections/{objection_id}/decide",
        headers=admin["headers"],
        json={"outcome": "dismissed", "resolution": "自己裁决自己"},
    )
    assert self_decide.status_code == 409

    blocked_archive = client.post(f"/api/evidence/packages/{package_id}/archive", headers=admin["headers"])
    assert blocked_archive.status_code == 409

    judge = _create_adjudicator(client, admin)
    decided = client.post(
        f"/api/evidence/objections/{objection_id}/decide",
        headers=judge["headers"],
        json={"outcome": "upheld", "resolution": "异议成立，引用范围已更正说明"},
    )
    assert decided.status_code == 200, decided.text
    again = client.post(
        f"/api/evidence/objections/{objection_id}/decide",
        headers=judge["headers"],
        json={"outcome": "dismissed", "resolution": "重复裁决"},
    )
    assert again.status_code == 409

    archived = client.post(f"/api/evidence/packages/{package_id}/archive", headers=admin["headers"])
    assert archived.status_code == 200


def test_verify_reports_broken_chain_and_digest_mismatch(client, admin):
    from app.database import get_connection

    package = _create_package(client, admin)
    package_id = package["id"]
    _append_entry(client, admin, package_id, "imp-tamper-1", content="原始正文一", reference="REF-1")
    _append_entry(client, admin, package_id, "imp-tamper-2", content="原始正文二", reference="REF-2")

    connection = get_connection()
    connection.execute("UPDATE evidence_nodes SET content='被篡改的正文' WHERE source_reference='REF-1'")
    connection.execute("UPDATE evidence_package_entries SET prev_entry_digest='deadbeef' WHERE position=2")

    verify = client.get(f"/api/evidence/packages/{package_id}/verify", headers=admin["headers"])
    assert verify.status_code == 200
    body = verify.json()
    assert body["ok"] is False
    kinds = {problem["kind"] for problem in body["problems"]}
    assert "evidence_digest_mismatch" in kinds
    assert "chain_broken" in kinds
    assert "entry_digest_mismatch" in kinds


def test_cli_verify_evidence_reports_problems(client, admin, monkeypatch, capsys):
    from app.cli import main as cli_main
    from app.database import get_connection

    package = _create_package(client, admin, code="PKG-CLI-01")
    _append_entry(client, admin, package["id"], "imp-cli-1")

    monkeypatch.setattr("sys.argv", ["cli", "verify-evidence", "--package", "PKG-CLI-01"])
    cli_main()
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["packages"][0]["checked_entries"] == 1

    get_connection().execute("UPDATE evidence_package_entries SET citation_scope='被改动的范围' WHERE position=1")
    try:
        cli_main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("摘要不匹配时离线校验必须返回非零退出码")
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["packages"][0]["problems"][0]["kind"] == "entry_digest_mismatch"
