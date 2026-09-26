from __future__ import annotations

import argparse
import json
import sqlite3

from fastapi.testclient import TestClient

from app.database import database_path, get_connection, init_db


def command_init() -> None:
    init_db()
    print(json.dumps({"database": str(database_path()), "initialized": True}, ensure_ascii=False))


def command_check() -> None:
    init_db()
    connection = get_connection()
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    print(json.dumps({"integrity": integrity, "foreign_keys": foreign_keys, "journal_mode": journal_mode}, ensure_ascii=False))
    if integrity != "ok" or foreign_keys != 1:
        raise SystemExit(1)


def command_smoke() -> None:
    from app.main import app

    with TestClient(app) as client:
        root = client.get("/")
        health = client.get("/api/system/health")
        print(json.dumps({"root": root.status_code, "health": health.status_code, "service": root.json().get("service")}, ensure_ascii=False))
        if root.status_code != 200 or health.status_code != 200:
            raise SystemExit(1)


def command_verify_evidence(package_code: str | None) -> None:
    from app.evidence.verification import verify_all_packages, verify_package

    init_db()
    connection = get_connection()
    if package_code:
        row = connection.execute("SELECT id FROM evidence_packages WHERE package_code=?", (package_code,)).fetchone()
        if row is None:
            print(json.dumps({"ok": False, "problems": [{"kind": "package_missing", "detail": f"证据包 {package_code} 不存在"}]}, ensure_ascii=False))
            raise SystemExit(1)
        reports = [verify_package(connection, int(row["id"]))]
    else:
        reports = verify_all_packages(connection)
    ok = all(report["ok"] for report in reports)
    print(json.dumps({"ok": ok, "packages": reports}, ensure_ascii=False, indent=2))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="知识产权档案服务维护命令")
    parser.add_argument("command", choices=("init-db", "check-db", "smoke", "verify-evidence"))
    parser.add_argument("--package", dest="package_code", default=None, help="仅校验指定编号的证据包")
    args = parser.parse_args()
    if args.command == "verify-evidence":
        command_verify_evidence(args.package_code)
    else:
        {"init-db": command_init, "check-db": command_check, "smoke": command_smoke}[args.command]()


if __name__ == "__main__":
    main()
