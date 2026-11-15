"""旧时空逻辑校验的无副作用封装。

以源仓库 ``logic_run_new.py`` 的当前有效链路为基础：把顶层脚本改写为
``run_logic_check(...)`` 函数，保留原队列/处理器/离散标签，地图能力
经 ``tools.geo``、模型能力经 ``tools.llm``。本方法明确为 legacy 兼容
迁移，不宣称修正历史算法口径。
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace

from tools import geo as geo_tools

from evaluation.logic_consistency.code.internal import processors
from evaluation.logic_consistency.code.internal.functions import preprocess
from evaluation.logic_consistency.code.internal.messages import (
    EventMessage, PersonaMessage,
)
from evaluation.logic_consistency.code.internal.model import Deepseek
from evaluation.logic_consistency.code.internal.queues import (
    APICallManager, TaskQueueManager,
)
from evaluation.logic_consistency.code.log_metrics import summarize_log

METHOD_NAME = "logic_consistency"
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

PROMPT_DEFAULTS = {
    "verify_1": os.path.join(PROMPT_DIR, "verify_1.txt"),
    "verify_2": os.path.join(PROMPT_DIR, "verify_2.txt"),
    "verify_3": os.path.join(PROMPT_DIR, "verify_3.txt"),
    "events_extraction": os.path.join(PROMPT_DIR, "events.txt"),
    "locations_extraction": os.path.join(PROMPT_DIR, "locations.txt"),
    "people_extraction": os.path.join(PROMPT_DIR, "people.txt"),
    "event_check": os.path.join(PROMPT_DIR, "check.txt"),
}

_REQUIRED_PROMPTS = tuple(PROMPT_DEFAULTS)

# 处理器使用类属性共享状态；每次运行前重置，避免同进程多次运行时串台。
_PROCESSOR_STATE = (
    ("ExtractProcessor", ("extract_apis", "locations_prompt",
                          "events_prompt", "people_prompt")),
    ("APIExtractProcessor", ("deepseek", "analyze_queue")),
    ("RelationAnalyzeProcessor", ("relations",)),
    ("LocationAnalyzeProcessor", ("gaode_api",)),
    ("EventAnalyzeProcess", ("deepseek", "calendar", "events_api",
                             "check_prompt")),
    ("APILocationAnalyzeProcessor", ("gaode_mid_stage", "limiter")),
    ("PathAnalyzeProcessor", ("gaode_api",)),
    ("APIPathAnalyzeProcessor", ("paths_analyze_queue", "limiter")),
    ("DeepseekPathAnalyzeProcess", ("deepseek_path_api", "paths_prompt")),
    ("APIDeepseekPathAnalyzeProcess", ("deepseek",)),
    ("APIEventAnalyzeProcess", ("deepseek",)),
)


def _reset_processor_state():
    for class_name, attrs in _PROCESSOR_STATE:
        cls = getattr(processors, class_name, None)
        if cls is None:
            continue
        for attr in attrs:
            setattr(cls, attr, None)


def _location_text(location):
    if isinstance(location, str):
        return location
    if isinstance(location, dict):
        parts = [location.get(k) for k in (
            "province", "city", "district", "street_name",
            "street_number", "poi")]
        return "".join(str(p) for p in parts if p)
    return "" if location is None else str(location)


def adapt_daily_event(data) -> dict:
    """把 ``daily_event.json`` 适配为旧逻辑使用的按日事件格式。

    旧格式: ``{"date": [{date: [{interval: {description, location}}]}]}``。
    已符合旧格式的输入原样返回。跨日事件按开始日期归属（与源数据
    生成方式一致）。无法解析 date 的输入记录为适配诊断。
    """
    if isinstance(data, dict) and isinstance(data.get("date"), list) and (
            not data["date"] or isinstance(data["date"][0], dict)):
        return data
    if not isinstance(data, list):
        raise ValueError("daily_event 必须是 JSON 数组或旧格式对象")

    by_date = defaultdict(list)
    diagnostics = []
    for index, event in enumerate(data):
        if not isinstance(event, dict):
            diagnostics.append({"index": index, "reason": "事件不是对象"})
            continue
        date_values = event.get("date")
        if not isinstance(date_values, list) or not date_values:
            diagnostics.append({"index": index,
                                "reason": "date 缺失或不是非空数组"})
            continue
        description = event.get("description") or ""
        location = _location_text(event.get("location"))
        for interval in date_values:
            if not isinstance(interval, str) or len(interval) < 10:
                diagnostics.append({"index": index,
                                    "reason": f"无法解析时间段 {interval!r}"})
                continue
            date_key = interval[:10]
            legacy_interval = interval.replace("至", "-", 1)
            by_date[date_key].append({
                legacy_interval: {
                    "description": description,
                    "location": location,
                }
            })
    # EventMessage expects each item to be a dict date -> list.
    adapted = {"date": [{d: by_date[d]} for d in sorted(by_date)]}
    adapted["adaptation_diagnostics"] = diagnostics
    return adapted


def adapt_persona(persona: dict) -> dict:
    """补齐旧逻辑必需字段，保留原始画像内容。"""
    if not isinstance(persona, dict):
        raise ValueError("persona 必须是 JSON 对象")
    adapted = dict(persona)
    adapted.setdefault("name", "person")
    adapted.setdefault("relation", [])
    for key in ("home_address", "workplace"):
        value = adapted.get(key)
        if not isinstance(value, dict):
            value = {}
        value = dict(value)
        value.setdefault("province", "")
        value.setdefault("city", "")
        value.setdefault("district", "")
        value.setdefault("street_name", "")
        value.setdefault("street_number", "")
        adapted[key] = value
    return adapted


def _make_logger(output_dir: str) -> logging.Logger:
    logger = logging.getLogger(f"logic_consistency.{output_dir}.{time.time()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(os.path.join(output_dir, "logic.log"),
                                  mode="a", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    return logger


def _monitor(logger, extracter, api_extracter_caller, analyzer,
             api_location_caller, path_analyzer, api_path_caller,
             deepseek_path_analyzer, deepseek_path_caller,
             extract_apis, analyze_queue, analyze_locations_queue,
             gaode_mid_stage_queue, analyze_paths_queue,
             deepseek_paths_queue, deepseek_paths_caller_queue,
             deadline: float):
    """原主监控循环；增加超时保护，避免线程异常后无限等待。"""
    while True:
        if time.time() > deadline:
            raise TimeoutError("旧时空校验处理超时，可能存在处理器线程异常")
        try:
            stop_condition_1 = True
            stop_condition_2 = False

            if extract_queue_empty(extracter) and \
                    extracter.all_processors_idle():
                if extracter.running.is_set():
                    print("通知Extracter Prompt组装停止")
                    extracter.stop()
                    print("完成所有提取Prompt组装")
                else:
                    extract_apis_is_empty = (
                        extract_apis["locations_queue"].empty()
                        and extract_apis["events_queue"].empty()
                        and extract_apis["relations_queue"].empty())
                    if extract_apis_is_empty and \
                            api_extracter_caller.all_processors_idle():
                        if api_extracter_caller.running.is_set():
                            print("通知Extracter API Caller停止")
                            api_extracter_caller.stop()
                            print("完成所有信息提取")
                        else:
                            if analyze_queue.empty() and \
                                    analyzer.all_processors_idle():
                                if analyzer.running.is_set():
                                    print("通知主 Analyzer停止")
                                    analyzer.stop()
                                    print("完成主分析过程")
                                else:
                                    if analyze_locations_queue.empty() and \
                                            api_location_caller.all_processors_idle():
                                        if api_location_caller.running.is_set():
                                            print("通知Location API Caller停止")
                                            api_location_caller.stop()
                                            print("完成地点检查")
                                        else:
                                            if gaode_mid_stage_queue.empty() and \
                                                    path_analyzer.all_processors_idle():
                                                if path_analyzer.running.is_set():
                                                    print("通知Path Prompt 组装停止")
                                                    path_analyzer.stop()
                                                    print("完成地点检查")
                                                else:
                                                    if analyze_paths_queue.empty() and \
                                                            api_path_caller.all_processors_idle():
                                                        if api_path_caller.running.is_set():
                                                            print("通知Path API Caller停止")
                                                            api_path_caller.stop()
                                                            print("完成Path查询")
                                                        else:
                                                            if deepseek_paths_queue.empty() and \
                                                                    deepseek_path_analyzer.all_processors_idle():
                                                                if deepseek_path_analyzer.running.is_set():
                                                                    print("通知Deepseek Path Prompt组装停止")
                                                                    deepseek_path_analyzer.stop()
                                                                    print("完成Deepseek Path提示组装")
                                                                else:
                                                                    if deepseek_paths_caller_queue.empty() and \
                                                                            deepseek_path_caller.all_processors_idle():
                                                                        if deepseek_path_caller.running.is_set():
                                                                            print("通知Deepseek Path API Caller停止")
                                                                            deepseek_path_caller.stop()
                                                                            print("完成Deepseek Path分析")
                                                                            stop_condition_2 = True
            if stop_condition_1 and stop_condition_2:
                break
        except Exception as exc:  # 保持源实现的兜底退出，但记录原因
            logger.exception("监控循环异常退出: %s", exc)
            break


def extract_queue_empty(extracter):
    """辅助: 提取队列由调用方闭包传入更直观，此函数保留可读性。"""
    return extracter.task_queue.empty()


def run_logic_check(person_file, event_file, output_dir, *,
                    start_date=None, end_date=None, options=None,
                    llm=None, geo_client=None, dry_run=False) -> dict:
    """执行单个人物的旧时空逻辑校验。

    ``llm`` 与 ``geo_client`` 仅供离线测试注入；生产分别使用
    ``Deepseek`` 与 ``tools.geo``。``dry_run=True`` 只构造任务并返回
    计划，不调用模型/地图、不写产物。
    """
    options = dict(options or {})
    with open(person_file, "r", encoding="utf-8") as f:
        persona_raw = json.load(f)
    with open(event_file, "r", encoding="utf-8") as f:
        event_raw = json.load(f)
    persona = adapt_persona(persona_raw)
    adapted_events = adapt_daily_event(event_raw)

    eventM = EventMessage(adapted_events)
    personaM = PersonaMessage(persona)

    all_dates = sorted(eventM.simplied_message.keys())
    if not all_dates:
        raise ValueError("事件数据中没有任何可解析的日期")
    start_date = start_date or all_dates[0]
    end_date = end_date or all_dates[-1]
    if start_date > end_date:
        raise ValueError(f"起始日期 {start_date} 晚于结束日期 {end_date}")

    num_workers = int(options.get("num_workers", 10))
    prompt_args = {key: options.get(key, default)
                   for key, default in PROMPT_DEFAULTS.items()}
    args = SimpleNamespace(
        start_date=start_date,
        end_date=end_date,
        event_file=event_file,
        person_file=person_file,
        log_file=os.path.join(output_dir, "logic.log"),
        save_dir=output_dir,
        num_workers=num_workers,
        **prompt_args,
    )
    for name in _REQUIRED_PROMPTS:
        path = getattr(args, name)
        if not os.path.exists(path):
            raise FileNotFoundError(f"缺少提示词文件: {path}")

    extract_queue = TaskQueueManager()
    calendar = preprocess(args, eventM, personaM, extract_queue)
    missing_dates = []
    cursor_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    while cursor_date <= end:
        cursor = cursor_date.strftime("%Y-%m-%d")
        if cursor not in calendar:
            missing_dates.append(cursor)
        cursor_date += timedelta(days=1)

    if dry_run:
        return {
            "person": personaM.origin_message.get("name"),
            "dry_run": True,
            "date_range": [start_date, end_date],
            "days_in_range": len([d for d in all_dates
                                  if start_date <= d <= end_date]),
            "tasks": len(calendar),
            "missing_dates": missing_dates,
            "adaptation_diagnostics":
                adapted_events.get("adaptation_diagnostics", []),
        }

    os.makedirs(output_dir, exist_ok=True)
    logger = _make_logger(output_dir)
    _reset_processor_state()
    processors.GEO_CLIENT = geo_client or geo_tools
    deepseek = llm or Deepseek(model=options.get("model", "deepseek-chat"))

    analyze_queue = TaskQueueManager()
    gaode_mid_stage_queue = TaskQueueManager()
    deepseek_paths_queue = TaskQueueManager()
    locations_queue = APICallManager(3)
    relations_queue = APICallManager(10)
    events_queue = APICallManager(10)
    analyze_events_queue = APICallManager(10)
    analyze_locations_queue = APICallManager(3)
    analyze_paths_queue = APICallManager(3)
    analyze_gaode_mid_stage = APICallManager(3)
    deepseek_paths_caller_queue = APICallManager(10)
    extract_apis = {
        "locations_queue": locations_queue,
        "relations_queue": relations_queue,
        "events_queue": events_queue,
    }
    analyze_apis = {
        "locations": analyze_locations_queue,
        "events": analyze_events_queue,
    }

    start = time.time()
    extracter = processors.ExtractProcessorCluster(
        num_workers, extract_queue, extract_apis, args)
    extracter.start()
    api_extracter_caller = processors.APIExtractProcessorCluster(
        num_workers, extract_apis, analyze_queue, deepseek, args)
    api_extracter_caller.start()
    analyzer = processors.AnalyzeProcessorCluster(
        num_workers, analyze_queue, analyze_apis, deepseek, calendar, args)
    analyzer.start()
    api_location_caller = processors.APILocationAnalyzeProcessorCluster(
        num_workers, analyze_locations_queue, gaode_mid_stage_queue)
    api_location_caller.start()
    path_analyzer = processors.PathAnalyzeProcessorCluster(
        num_workers, gaode_mid_stage_queue, analyze_paths_queue)
    path_analyzer.start()
    api_path_caller = processors.APIPathAnalyzeProcessorCluster(
        num_workers, analyze_paths_queue, deepseek_paths_queue)
    api_path_caller.start()
    deepseek_path_analyzer = processors.DeepseekPathAnalyzeProcessCluster(
        num_workers, deepseek_paths_queue, deepseek_paths_caller_queue, args)
    deepseek_path_analyzer.start()
    deepseek_path_caller = processors.APIDeepseekPathAnalyzeProcessCluster(
        num_workers, deepseek_paths_caller_queue, None, deepseek)
    deepseek_path_caller.start()

    timeout = float(options.get("timeout_seconds", 3600))
    _monitor(
        logger, extracter, api_extracter_caller, analyzer,
        api_location_caller, path_analyzer, api_path_caller,
        deepseek_path_analyzer, deepseek_path_caller,
        extract_apis, analyze_queue, analyze_locations_queue,
        gaode_mid_stage_queue, analyze_paths_queue,
        deepseek_paths_queue, deepseek_paths_caller_queue,
        time.time() + timeout)
    elapsed = time.time() - start

    calendar_dir = os.path.join(output_dir, "calendar",
                                str(personaM.origin_message.get("name")))
    os.makedirs(calendar_dir, exist_ok=True)
    task_results = []
    for date, day in calendar.items():
        with open(os.path.join(calendar_dir, f"{day.date}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(day.to_dict(), f, ensure_ascii=False, indent=4)
        day.write_log(logger)
        task_results.append(day.to_dict())
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)

    log_summary = summarize_log(os.path.join(output_dir, "logic.log"))
    metrics = {
        "method": METHOD_NAME,
        # 源实现会跳过范围内没有事件数据的日期并继续；缺失日期只作
        # 诊断信息，不把合法空档视为技术失败。
        "status": "complete",
        "person": personaM.origin_message.get("name"),
        "date_range": [start_date, end_date],
        "elapsed_seconds": round(elapsed, 3),
        "tasks": len(calendar),
        "missing_dates": missing_dates,
        "adaptation_diagnostics":
            adapted_events.get("adaptation_diagnostics", []),
        "log_aggregation": log_summary,
        "task_results": task_results,
        "legacy": True,
    }
    with open(os.path.join(output_dir, "metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    return metrics


__all__ = [
    "run_logic_check", "adapt_daily_event", "adapt_persona",
    "PROMPT_DEFAULTS",
]
