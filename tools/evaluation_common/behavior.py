"""行为分析方法的共用编排（照片/SMS 与外部参照对比）。

六个行为分析共享同一输入形状：单人物数据文件 + ATM 参照文件。
本模块只负责人物发现、参照文件解析、随机种子与 metrics 落盘，
具体指标仍由各方法的 engine 计算。
"""
from __future__ import annotations

import os
import random

from tools.evaluation_common.config import resolve_method_options
from tools.evaluation_common.data_loader import discover_persons
from tools.evaluation_common.runner import ensure_output_dir, write_metrics


def resolve_reference(options: dict, *, key: str, default_name: str,
                      label: str) -> str:
    """解析 ATM 参照文件。

    优先级：显式文件参数 > ``atm_root`` 下的默认文件名。
    缺失时抛出 FileNotFoundError，明确指出缺哪个文件。
    """
    explicit = options.get(key)
    if explicit:
        if not os.path.exists(explicit):
            raise FileNotFoundError(f"缺少{label}文件: {explicit}")
        return explicit
    atm_root = options.get("atm_root")
    if not atm_root:
        raise FileNotFoundError(
            f"缺少{label}：请提供 options['{key}'] 或 options['atm_root']"
            f"（其下应包含 {default_name}）")
    path = os.path.join(atm_root, default_name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"缺少{label}文件: {path}（ATM 参照目录不完整）")
    return path


def evaluate_comparison(
    method_name: str,
    defaults: dict,
    data_dir,
    output_dir,
    *,
    options=None,
    person_relative="phone_data/photo.json",
    reference_key="atm_file",
    reference_default="image_batch_results.json",
    reference_label="ATM 图像参照",
    run_person,
) -> dict:
    """运行一个“人物文件 vs ATM 参照”行为分析方法。

    ``run_person(input_file, reference_file, person_out, opts)`` 返回
    engine 结果列表；本函数负责逐人输出 ``metrics.json`` 与根汇总。
    """
    opts = resolve_method_options(method_name, defaults, options)
    persons = discover_persons(data_dir, (person_relative,))
    if not persons:
        raise ValueError(
            f"{data_dir} 下未发现包含 {person_relative} 的人物目录")
    ensure_output_dir(output_dir)

    seed = opts.get("seed")
    entries = {}
    failures = []
    total_results = 0
    for name, person_dir in persons:
        person_out = os.path.join(output_dir, name)
        ensure_output_dir(person_out)
        explicit_input = opts.get("input_file")
        input_file = explicit_input or os.path.join(person_dir, person_relative)
        try:
            if not os.path.exists(input_file):
                raise FileNotFoundError(f"缺少输入文件: {input_file}")
            reference_file = resolve_reference(
                opts, key=reference_key, default_name=reference_default,
                label=reference_label)
            if seed is not None:
                random.seed(seed)
            if opts.get("dry_run"):
                import json
                with open(input_file, "r", encoding="utf-8") as f:
                    input_count = len(json.load(f))
                with open(reference_file, "r", encoding="utf-8") as f:
                    reference_count = len(json.load(f))
                metrics = {
                    "method": method_name,
                    "status": "dry_run",
                    "person": name,
                    "inputs": {
                        "input_file": os.path.abspath(input_file),
                        "reference_file": os.path.abspath(reference_file),
                        "input_count": input_count,
                        "reference_count": reference_count,
                    },
                    "results": [],
                }
            else:
                results = run_person(input_file, reference_file,
                                     person_out, opts)
                metrics = {
                    "method": method_name,
                    "status": "complete",
                    "person": name,
                    "inputs": {
                        "input_file": os.path.abspath(input_file),
                        "reference_file": os.path.abspath(reference_file),
                    },
                    "results": results,
                }
        except Exception as exc:
            metrics = {
                "method": method_name,
                "status": "error",
                "person": name,
                "error": f"{type(exc).__name__}: {exc}",
                "results": [],
            }
            failures.append(name)
        metrics_path = write_metrics(person_out, metrics)
        entries[name] = {
            "status": metrics["status"],
            "metrics_path": metrics_path,
            "result_count": len(metrics.get("results") or []),
            "error": metrics.get("error"),
        }
        total_results += len(metrics.get("results") or [])

    status = "incomplete" if failures else "complete"
    if entries and all(e["status"] == "dry_run" for e in entries.values()):
        status = "dry_run"
    root_metrics = {
        "method": method_name,
        "status": status,
        "persons": entries,
        "counts": {
            "persons": len(entries),
            "failed_persons": len(failures),
            "result_sections": total_results,
        },
        "notes": ("各人物结果原样保留在 persons.<name>.metrics_path；"
                  "未定义跨人物总体指标，不合并比例或创造通过阈值"),
    }
    metrics_path = write_metrics(output_dir, root_metrics)
    return {
        "method": method_name,
        "status": status,
        "dry_run": status == "dry_run",
        "persons": entries,
        "failed_persons": failures,
        "metrics_path": metrics_path,
        "output_dir": os.path.abspath(output_dir),
    }


def sms_reference(options: dict) -> str:
    return resolve_reference(options, key="email_file",
                             default_name="emails.json",
                             label="ATM 邮件参照")


def photo_reference(options: dict) -> str:
    return resolve_reference(options, key="atm_file",
                             default_name="image_batch_results.json",
                             label="ATM 图像参照")


__all__ = [
    "resolve_reference", "evaluate_comparison", "sms_reference",
    "photo_reference",
]
