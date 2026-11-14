# 五类评估索引

各方法位于独立子目录，模型、文件读写和配置的共享实现位于 `../tools/`。复算报告默认写入 `output/`；`../results/` 与 `geolocation/results/` 是本分支保存的历史结果，不会在下列命令中被覆盖。

| 类别 | 主要代码 | 已保存结果 | 输入 |
|---|---|---|---|
| 地理 | `geolocation/scripts/run_evaluation.py` | `geolocation/results/*.json` | `geolocation/results/*.npz` 合成轨迹 + 本地外部参考 NPZ |
| 事件多样性 | `event_diversity/method.py` | `../results/event_diversity/` | 原始版及三种消融的 `daily_event.json` |
| 兴趣漂移 | `interest_stats/run_ablation_comparison.py` | `../results/interest_stats/` | 原始版、去规划版和模拟基线的 `daily_event.json` |
| 逻辑一致性 | `logic_consistency/legacy_two_metrics_90day.py` | `../results/logic_consistency/` | 原始版和仅客观版的画像、日事件与抽样日清单 |
| 时间使用 | `time_use/run_ablation_comparison.py` | `../results/time_use/` | 原始版、去规划版和模拟基线的日事件 |

## 运行命令

从仓库根目录运行。模型类评估需先复制并填写 `config/config.example.json` 为 `config/config.json`。

### 地理

```bash
python evaluation/geolocation/scripts/run_evaluation.py --dry-run
```

此命令会检查外部参考 NPZ 是否齐全。缺少文件时参照根目录 README 的路径，用 `geolocation/scripts/prepare_reference_data.py` 和 `prepare_mobile_reference_data.py` 准备数据，再运行相同命令（去掉 `--dry-run`）。历史报告保存在 `geolocation/results/`，其中 `lifebench_5p_300d_new.jsonl` 和 `.npz` 是合成轨迹输入。

### 事件多样性

```bash
python -m evaluation.event_diversity.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/event_diversity/original
python -m evaluation.event_diversity.method --data-dir data/ablation_fenghaoran_2025/objective_only/fenghaoran --output-dir output/event_diversity/objective_only
```

`results/event_diversity/` 还保存去规划版与模拟基线版指标。其输入目录分别为 `data/ablation_fenghaoran_2025/no_planning/fenghaoran` 和 `data/ablation_fenghaoran_2025/simulation_baseline/fenghaoran`。已保存结果使用旧版 formatter 对齐后的输入，若改动数据或统计窗口，数值会变化。

### 兴趣漂移

```bash
python evaluation/interest_stats/run_ablation_comparison.py --dry-run
python evaluation/interest_stats/run_ablation_comparison.py --max-workers 8
```

默认窗口为 2025-09-10 至 2025-12-08，共 90 天。`results/interest_stats/comparison.json` 与 `.md` 是三组汇总，各组目录内保存逐事件抽取报告和缓存。更多参数见 [interest_stats/README.md](interest_stats/README.md)。

### 逻辑一致性

```bash
python -m evaluation.logic_consistency.legacy_two_metrics_90day --help
python -m evaluation.logic_consistency.legacy_two_metrics_90day --version both
```

默认使用 `results/logic_consistency/manifest.json` 中的同一批 90 天，计算原始版和仅客观版的 `event_consistency_rate`、`person_consistency_rate`，不调用地图评分。两个目录均有汇总 `metrics.json` 和逐日判定。该方法需要模型，运行参数与局限见 [logic_consistency/README.md](logic_consistency/README.md)。

### 时间使用

```bash
python evaluation/time_use/run_ablation_comparison.py --dry-run
python evaluation/time_use/run_ablation_comparison.py --sleep-policy night-gap --max-workers 8
```

默认统计 2025 全年，六领域时间使用结果在 `results/time_use/`，包含三组汇总、逐日分配及失败记录。完整口径见 [time_use/README.md](time_use/README.md)。

## 数据范围

冯浩然及消融输入为合成数据。地理参考集是外部真实数据，不在本分支；OmniBehavior 用户行为数据也不在本分支。重新运行模型评估需要自行配置 API 凭据，既有结果可直接离线查看。