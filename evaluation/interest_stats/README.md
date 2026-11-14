# 冯浩然兴趣漂移评估

本目录保留原始版、去规划版和模拟基线版的兴趣抽取与连续日漂移评估代码。合成输入在仓库根目录 `data/`，已保存的三组报告与缓存在 `results/interest_stats/`。OmniBehavior 用户行为输入及其他外部数据集不在本分支。

## 指标

评估器对每条事件抽取一个主要兴趣类别，将同一天出现的类别组成集合；相邻两日的漂移定义为 `1 − Jaccard(当日集合, 次日集合)`。报告同时给出各日类别、相邻日漂移序列、均值和类别分布。使用固定类别表与提示词版本 `lifebench-interest-v1`。不同模型或提示词得到的抽取可能不同，不能把漂移大小直接当作真实性高低。

## 运行

从仓库根目录执行。先复制 `config/config.example.json` 为 `config/config.json` 并填写 `llm.api_key`，或设置 `DEEPSEEK_API_KEY`。`--dry-run` 只检查输入与预计请求数。

```bash
python evaluation/interest_stats/run_ablation_comparison.py --dry-run
python evaluation/interest_stats/run_ablation_comparison.py --max-workers 8
```

默认比较 2025-09-10 至 2025-12-08 的 90 个日历日。默认结果写在被忽略的 `output/interest_stats/ablation_comparison_20250910_20251208/`。已保存的抽取缓存可作为种子，但每条记录仍须通过事件内容、模型、提示词和类别表哈希校验。用 `--refresh` 强制重抽取。

单独评估合成模拟基线的指定 90 天窗口：

```bash
python evaluation/interest_stats/run_simulation_baseline_90d.py --dry-run
python evaluation/interest_stats/run_simulation_baseline_90d.py --max-workers 8
```

核心评估器 `eval/drift_eval/lifebench_interest_drift_eval.py` 也可单独运行，默认输入为仓库中的冯浩然 `daily_event.json`。可使用 `--input`、`--from-date`、`--to-date`、`--output` 指定其他合成输入与窗口。

## 已保存结果

- `results/interest_stats/comparison.json`、`comparison.md`：三组汇总；
- `results/interest_stats/<组名>/interest_drift.json`：逐事件抽取与逐日统计；
- `results/interest_stats/<组名>/interest_drift.cache.json`：可校验的抽取缓存。

原始输入、结果和缓存均为合成冯浩然数据。报告含历史模型名称和运行参数；复算时请记录实际使用的模型版本。