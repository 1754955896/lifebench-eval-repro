"""方法配置解析。

优先级：显式 ``options`` / CLI > ``config/config.json`` 的
``evaluation.<method_name>`` > 方法默认值。配置文件中的相对路径按目标
仓库根目录解析；调用方显式传入的路径按调用方当前目录解析。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "config.json"
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "config.example.json"

_PATH_KEYS = ("_file", "_dir", "_root", "_path", "prompt")


def load_config() -> dict:
    """读取本地配置；缺失时回退到模板。始终返回 dict。"""
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_path_key(key: str) -> bool:
    if key == "reuse_samples_from":
        return True
    return any(key.endswith(suffix) for suffix in _PATH_KEYS)


def _resolve_config_path(value):
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str(REPO_ROOT / path)


def method_config(method_name: str) -> dict:
    """返回 ``evaluation.<method_name>`` 配置（相对路径已按仓库根解析）。"""
    section = (load_config().get("evaluation") or {}).get(method_name) or {}
    if not isinstance(section, dict):
        return {}
    resolved = {}
    for key, value in section.items():
        resolved[key] = _resolve_config_path(value) if _is_path_key(key) else value
    return resolved


def resolve_method_options(method_name: str, defaults: dict,
                           options: dict | None = None) -> dict:
    """按优先级合并方法配置。

    配置中的路径按仓库根解析；显式 options 原样覆盖（不做 rebase）。
    """
    merged = dict(defaults)
    merged.update(method_config(method_name))
    if options:
        merged.update({k: v for k, v in options.items() if v is not None})
    return merged


def repo_path(*parts) -> str:
    return str(REPO_ROOT.joinpath(*parts))


def resolve_cli_path(value, default=None):
    """CLI/调用方路径：相对路径保留（按当前工作目录解释）。"""
    if value is None:
        return default
    return os.path.expanduser(str(value))


__all__ = [
    "REPO_ROOT", "CONFIG_PATH", "EXAMPLE_CONFIG_PATH", "load_config",
    "method_config", "resolve_method_options", "repo_path",
    "resolve_cli_path",
]
