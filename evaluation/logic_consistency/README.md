# 逻辑一致性评估

## 目录

| 路径 | 用途 |
| --- | --- |
| `evaluate.py` | 统一命令行及 Python 入口 |
| `code/pipeline.py`、`code/runner.py` | 输入适配、任务执行和结果汇总 |
| `code/event/score.py` | 事件安排一致率 |
| `code/person/score.py` | 人物关系一致率 |
| `code/location/score.py` | 地点真实率 |
| `code/path/score.py` | 路径真实率 |
| `code/relation_update/` | 动态关系库评估与关系一致率（含原管线及提示词） |
| `code/internal/`、`code/prompts/` | 处理器、模型接口与提示词 |

## 运行

从仓库根目录执行。人物目录须包含 `daily_event.json` 和 `persona.json`。先安装仓库根目录 `requirements.txt` 中的依赖，并配置模型及地图服务。

```bash
python -m evaluation.logic_consistency.evaluate \
  --data-dir data/lifebench_10people/fenghaoran \
  --output-dir output/logic_consistency \
  --scores event person
```

`--scores` 后列出要计入最终分数的方法，可从 `event`、`person`、`location`、`path`、`relation_update` 中选一个或多个，顺序不限，不能重复；省略时仍选原有四项。选中 `relation_update` 时，人物目录还须包含 `event_tree.json`。示例：只评动态关系用 `--scores relation_update`；与原有计分组合用 `--scores event person relation_update`。`--start-date`、`--end-date` 指定日期范围，`--persons` 可从数据集目录中限定人物，`--dry-run` 只检查和计划任务，不调用模型或地图。也可用 `from evaluation.logic_consistency.evaluate import evaluate` 在 Python 中调用，并在 `options` 中传入 `scoring_methods` 列表。

选中原有四项中的任意一项时，原处理链会生成完整的事件、人物、地点和路径判定；只选 `relation_update` 时，仅运行动态关系管线。动态关系管线逐日维护关系库，来源事件可更新关系，噪声事件不进入后续事实。`--relation-model`、`--relation-max-model-calls`、`--relation-max-retries` 和 `--relation-resume` 可配置该管线；其产物位于逐人目录下的 `relation_update/`。各方法计分口径：

- `event` = 事件安排正确 / (正确 + 错误)；疑似错误不计入分母。
- `person` = 人物关系正确 / (正确 + 错误)。
- `location` = 地点验证成功 / (成功 + 疑似错误)。
- `path` = (路径查询失败 + 成功 − 路径时间错误) / (成功 + 失败)。
- `relation_update` = conform / (conform + nonconform)；unknown 和 not_applicable 不计入分母。

逐人 `scoring.final_score` 是所选方法分数的**等权算术平均**；根目录 `metrics.json` 的 `final_score` 再对所有人物等权求平均。任何所选方法分母为 0 时，该方法分数为 `null`，相应人物及总分也为 `null`，状态标为 `incomplete`，不会悄悄剔除。逐人 `metrics.json` 同时保留原始离散判定和日志计数，便于复核。
