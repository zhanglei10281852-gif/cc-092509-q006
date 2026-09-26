"""证据链的规范化序列化与哈希计算。

证据包条目只追加、不可替换原文，因此每条记录都携带前一条的哈希，
形成按来源登记顺序排列的哈希链；归档时再对整条链计算归档摘要。
校验命令与 HTTP 接口都复用这里的算法，避免两套实现给出不同结论。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CHAIN_VERSION = "evidence-chain-v1"
ARCHIVE_VERSION = "evidence-archive-v1"
GENESIS_SEED = "evidence-genesis-v1"


def canonical_json(value: Any) -> str:
    """对任意 JSON 兼容内容生成确定性序列化结果。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_content(value: Any) -> str:
    """计算证据原始内容的摘要，作为"当时看到的内容"的指纹。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def genesis_hash(package_code: str) -> str:
    """每个证据包独立的创世哈希，避免跨包拼接伪造。"""
    return hashlib.sha256(f"{GENESIS_SEED}:{package_code}".encode("utf-8")).hexdigest()


def hash_entry(
    *,
    package_code: str,
    seq: int,
    entry_kind: str,
    evidence_id: int,
    content_digest: str,
    quote_range: str,
    summary: str,
    note: str,
    appended_by: int | None,
    created_at: str,
    prev_hash: str,
) -> str:
    """根据条目不可变字段计算链上哈希。"""
    material = "\n".join(
        [
            CHAIN_VERSION,
            package_code,
            str(seq),
            entry_kind,
            str(evidence_id),
            content_digest,
            quote_range,
            summary,
            note,
            str(appended_by if appended_by is not None else ""),
            created_at,
            prev_hash,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def archive_digest(package_code: str, entry_count: int, head_hash: str | None) -> str:
    """归档摘要覆盖条目数量与链头，归档后追加任何条目都会失配。"""
    material = "\n".join([ARCHIVE_VERSION, package_code, str(entry_count), head_hash or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
