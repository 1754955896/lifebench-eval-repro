"""共享签名与运行隔离辅助。

- 文件哈希、稳定 JSON 序列化与对象哈希；
- 通用 run_manifest 签名一致性检查与显式迁移。
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime


def stable_dumps(obj) -> str:
    """确定性 JSON 序列化（固定插入序、无多余空白）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def stable_hash(obj) -> str:
    """对象稳定哈希（sha256）。"""
    return hashlib.sha256(stable_dumps(obj).encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    """文件内容 sha256。"""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_run_compatibility(store, signature: dict,
                            manifest_name: str = "run_manifest.json") -> str:
    """检查输出目录既有 manifest 是否与当前签名一致。

    返回 ``"ok"`` / ``"manifest_missing"`` / ``"manifest_damaged"`` /
    ``"manifest_incomplete"``。签名不一致时抛出 ValueError：同一输出
    目录不允许混入不同判定规则的结果。
    """
    path = store._path(manifest_name)
    if not os.path.exists(path):
        return "manifest_missing"
    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return "manifest_damaged"
    if not isinstance(manifest, dict) \
            or not isinstance(manifest.get("evaluation_signature"), dict):
        return "manifest_incomplete"
    stored = manifest["evaluation_signature"]
    if stored != signature:
        diffs = sorted({k for k in set(stored) | set(signature)
                        if stored.get(k) != signature.get(k)})
        raise ValueError(
            f"输出目录 {store.out_dir} 已存在其他运行的结果，"
            f"评估签名不一致: {', '.join(diffs)}。"
            "请更换 --output-dir，避免不同判定规则的结果混入同一份输出。")
    return "ok"


def migrate_manifest_from_records(store, signature: dict,
                                  is_valid_record,
                                  manifest_name: str = "run_manifest.json",
                                  record_name: str = "events.jsonl",
                                  schema_version=None) -> None:
    """从既有事件记录显式重建 manifest。

    仅当全部有效记录的 ``evaluation_signature`` 与当前签名完全一致时
    才允许，避免把不同判定规则下的结果并入当前运行。
    """
    configs = set()
    valid = 0
    path = store._path(record_name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if (is_valid_record(rec)
                        and isinstance(rec.get("evaluation_signature"), dict)):
                    valid += 1
                    configs.add(json.dumps(rec["evaluation_signature"],
                                           sort_keys=True,
                                           ensure_ascii=False))
    if valid == 0:
        raise ValueError(
            f"输出目录 {store.out_dir} 的 {record_name} 中没有可迁移的"
            "有效结果记录，无法重建 manifest。")
    current = json.dumps(signature, sort_keys=True, ensure_ascii=False)
    if configs != {current}:
        raise ValueError(
            f"输出目录 {store.out_dir} 的既有结果记录与当前评估签名不一致，"
            "无法安全迁移；请更换 --output-dir。")
    manifest = {
        "evaluation_signature": signature,
        "migrated_from_records": True,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    if schema_version is not None:
        manifest["schema_version"] = schema_version
    store.write_json(manifest_name, manifest)


__all__ = [
    "stable_dumps", "stable_hash", "sha256_file", "sha256_text",
    "check_run_compatibility", "migrate_manifest_from_records",
]
