# 评估脚本

从仓库根目录运行。需要调用模型的脚本使用 `config/config.json` 或对应环境变量读取凭据；具体参数见各方法的 README 与 `--help`。

| 方法 | 入口 | 输入 |
| --- | --- | --- |
| 地理 | `geolocation/reproduce_table.py`、`geolocation/reproduce_descriptive_table.py` | `geolocation/data/` 中的 NPZ |
| 事件多样性 | `event_diversity/method.py` | 人物目录中的 `daily_event.json` |
| 兴趣漂移 | `interest_stats/evaluate.py` | `interest_stats/data/input/`、`interest_stats/data/output/` |
| 逻辑一致性 | `logic_consistency/evaluate.py` | `daily_event.json`、`persona.json`；选择 `relation_update` 时还需 `event_tree.json` |
| 时间使用 | `time_use/method.py` | 人物目录中的 `daily_event.json` |

## 运行示例

```bash
python evaluation/geolocation/reproduce_table.py
python evaluation/geolocation/reproduce_descriptive_table.py
python -m evaluation.event_diversity.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/event_diversity
python evaluation/interest_stats/evaluate.py
python -m evaluation.logic_consistency.evaluate --data-dir data/lifebench_10people/fenghaoran --output-dir output/logic_consistency --scores event person --dry-run
python -m evaluation.time_use.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/time_use --dry-run
```

地理脚本把表格写入 `evaluation/geolocation/`；兴趣漂移脚本默认从保存的输入和分类结果重算。其他示例把运行产物写入 `output/`。逻辑一致性及时间使用示例中的 `--dry-run` 用于检查输入，正式运行时移除此参数。

各方法的参数与计分口径见 [geolocation/README.md](geolocation/README.md)、[event_diversity/README.md](event_diversity/README.md)、[interest_stats/README.md](interest_stats/README.md)、[logic_consistency/README.md](logic_consistency/README.md) 和 [time_use/README.md](time_use/README.md)。
