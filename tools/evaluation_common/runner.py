"""方法入口共用的小工具：输出目录、metrics 落盘、批量人物编排。

不建设注册器或调度框架，只提供各方法 ``method.py`` 共用的少量 I/O 与
状态汇总函数。
"""
from __future__ import annotations

import os

from tools.evaluation_common.storage import write_json


def ensure_output_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def write_metrics(output_dir: str, metrics: dict, name: str = "metrics.json") -> str:
    path = os.path.join(output_dir, name)
    write_json(path, metrics)
    return path


def run_over_persons(persons, output_dir, run_one):
    """对 ``[(name, person_dir), ...]`` 逐个运行 ``run_one``。

    ``run_one(name, person_dir, person_output_dir)`` 返回 dict（可含
    ``status``、``dry_run``、``metrics``、``artifacts``）。返回批量汇总。
    """
    ensure_output_dir(output_dir)
    entries = []
    failures = []
    for name, person_dir in persons:
        person_out = os.path.join(output_dir, name)
        ensure_output_dir(person_out)
        try:
            result = run_one(name, person_dir, person_out)
        except Exception as exc:  # 技术错误不生成语义标签
            entries.append({
                "person": name,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "output_dir": person_out,
            })
            failures.append(name)
            continue
        metrics = result.pop("metrics", None)
        if metrics is not None:
            result["metrics_path"] = write_metrics(person_out, metrics)
            result["metrics"] = metrics
        entries.append({"person": name, "output_dir": person_out, **result})
        if result.get("status") not in ("complete", "dry_run"):
            failures.append(name)

    status = "complete"
    if failures:
        status = "incomplete"
    if entries and all(e.get("status") == "dry_run" for e in entries):
        status = "dry_run"
    return {
        "status": status,
        "persons": entries,
        "failed_persons": failures,
    }


__all__ = [
    "ensure_output_dir", "write_metrics", "run_over_persons",
]
