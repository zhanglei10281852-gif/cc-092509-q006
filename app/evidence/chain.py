from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def evidence_content_digest(source_kind: str, source_reference: str, content: str) -> str:
    """证据节点内容摘要：同一来源同一正文永远得到同一摘要，用于去重与事后核对。"""
    canonical = _canonical(
        {
            "kind": "evidence-node",
            "source_kind": source_kind,
            "source_reference": source_reference,
            "content": content,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def entry_chain_digest(
    *,
    package_code: str,
    position: int,
    content_digest: str,
    citation_scope: str,
    interpretation: str,
    prev_entry_digest: str,
    appended_at: str,
) -> str:
    """条目链摘要：把前一条目的摘要纳入计算，任何篡改或断链都会导致重算结果不一致。"""
    canonical = _canonical(
        {
            "kind": "evidence-entry",
            "package_code": package_code,
            "position": position,
            "content_digest": content_digest,
            "citation_scope": citation_scope,
            "interpretation": interpretation,
            "prev_entry_digest": prev_entry_digest,
            "appended_at": appended_at,
        }
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
