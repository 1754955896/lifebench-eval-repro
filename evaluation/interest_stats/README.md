# 兴趣漂移评估脚本

从仓库根目录运行：

```bash
python evaluation/interest_stats/evaluate.py
```

脚本默认读取 `evaluation/interest_stats/data/manifest.json`。清单中的 `normalized_input` 和 `coarse_report` 是相对于 `data/` 的路径：前者提供逐事件记录，后者提供 `event_extractions` 中的逐事件兴趣类别。脚本按 `event_id` 关联两者，计算并核对兴趣漂移指标。整个过程只使用 Python 标准库，不调用模型。

如需指定另一套同结构的数据目录或保存重算明细：

```bash
python evaluation/interest_stats/evaluate.py --data-dir PATH_TO_DATA
python evaluation/interest_stats/evaluate.py --output output/interest_stats/recomputed.json
```

`data/input/<dataset>/<sample>.json` 中每条记录至少需要 `event_id` 和 `date`；`data/output/<dataset>/<sample>.json` 中需要同一批事件的 `event_extractions`、`provenance`、`daily_profiles`、`coverage` 和 `interest_drift`，供脚本核对。`data/manifest.json` 为每个样本指定上述两个文件及 `event_count`。

脚本将每天 `interest_signal=true` 的类别去重形成集合，只比较日历上连续的两天，计算 `1 − |交集| / |并集|`。两天集合都为空时跳过；仅一天为空时漂移为 1。逐日漂移保留 6 位小数，再计算个人及数据集层面的汇总值。
