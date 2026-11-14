# logic_consistency — 旧时空逻辑校验（Legacy）

## 本分支已保存的两项指标

`results/logic_consistency/` 包含原始版与仅客观版同一批 90 天的
`event_consistency_rate`、`person_consistency_rate`，以及逐日判定。运行
`python -m evaluation.logic_consistency.legacy_two_metrics_90day --version both`
可按 `results/logic_consistency/manifest.json` 复算；此入口不计算地图指标。

> **Legacy 状态。** 本方法由源仓库 `logic_run_new.py` 当前有效链路封装
> 而来，保留原队列/处理器/日志聚合与历史统计公式；本次迁移不宣称修复
> 既有算法缺陷，也不把兼容迁移表述为质量指标。

## 流程

1. 确定性输入适配：`daily_event.json` → 旧逻辑按日事件格式
   （`{"date": [{date: [{interval: {description, location}}]}]}`）；
   跨日事件按开始日期归属。
2. 信息提取：地点/路径、事件、人物关系三路提示词调用。
3. 主分析：人物关系匹配、地点地理编码、事件时间安排检查。
4. 路径分析：高德路径查询 + 模型路径合理性判断。
5. 汇总：按日保存 `calendar/<person>/<date>.json`、日志与结构化 metrics。

## 指标

- `task_results`：逐日任务的原离散结果（事件、地点、人物关系、路径、
  事件安排结论、路径分析结论）。
- `log_aggregation.counts`：日志模式出现次数（时间提取错误、地点正确/
  疑似错误、人物正确/错误、路径查询成功/失败、事件安排正确/错误、
  路径时间错误）。
- `log_aggregation.rates`：
  - 事件一致率 = 正确 / (正确 + 错误)
  - 人物一致率 = 正确 / (正确 + 错误)
  - 地点真实率 = 成功 / (成功 + 疑似错误)
  - `path_truth_rate_legacy` = (失败 + 成功 − 路径时间错误) / (成功 + 失败)
- `missing_dates`：日期范围内没有事件数据的日期。
- `adaptation_diagnostics`：无法解析 date 的输入诊断。

## 输入

人物目录需含：

- `daily_event.json`
- `persona.json`（`name`、`home_address`、`workplace`、`relation`；
  缺失的关系/地址字段会补空值并保留原内容）

## 输出

- `<output_dir>/<person>/metrics.json`
- `<output_dir>/<person>/logic.log`
- `<output_dir>/<person>/calendar/<person_name>/<date>.json`
- `<output_dir>/metrics.json`

## 运行

```bash
python -m evaluation.logic_consistency.method \
  --data-dir data/lifebench_10people/fenghaoran \
  --output-dir output/logic_consistency --dry-run

python -m evaluation.logic_consistency.method \
  --data-dir data/lifebench_10people/fenghaoran \
  --output-dir output/logic_consistency \
  --start-date 2025-01-01 --end-date 2025-01-07 --num-workers 10
```

```python
from evaluation.logic_consistency.method import evaluate
result = evaluate("data/lifebench_10people/fenghaoran",
                  "output/logic_consistency")
```

## 模型、地图与配置

- 默认模型 `deepseek-chat`（保留源选择，显式进入方法配置）。
- 配置优先级：显式 options/CLI > `evaluation.logic_consistency` > 默认值。
- 模型经 `tools/llm.chat_completion`；地图经 `tools/geo` 的
  `geocode`/`route`。方法内部不创建私有客户端、不硬编码高德密钥。
- 提示词原文位于 `prompts/`（`verify_1/2/3`、`events`、`locations`、
  `people`、`check`）。
- `--dry-run` 只适配输入、构造任务并返回计划，不调用模型/地图、不写产物。

## 已知限制（保留，不静默修正）

- **目标日前后窗口取数偏差**：事件安排检查按目标日期 ±2 天取数，沿用
  原实现。
- **事件疑似错误不入分母**：日志一致率分母只含"正确 + 错误"，疑似错误
  不计入分母，沿用原统计。
- **路径真实率历史公式**：见上，公式保留为 `path_truth_rate_legacy`；
  修复需要独立的语义变更与回归测试。
- 时间提取失败、地图失败等错误会以原日志/结果标签记录；地图失败写入
  `<地点错误>`，路径失败写入 `<路径时间查询错误>`，不中断整条链路。
- 旧链路没有断点续跑语义：重复运行会重新执行整个日期窗口并追加
  `logic.log`；本次迁移未新增续跑机制。
- 本次迁移未执行真实模型/地图验证；离线测试全部使用假对象。
