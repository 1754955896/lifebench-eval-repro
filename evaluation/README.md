# Evaluation Scripts

Run commands from the repository root. Each method's README lists its inputs and options.

| Method | Instructions |
| --- | --- |
| Geolocation | [geolocation/](geolocation/README.md) |
| Event diversity | [event_diversity/](event_diversity/README.md) |
| Interest drift | [interest_stats/](interest_stats/README.md) |
| Logic consistency | [logic_consistency/](logic_consistency/README.md) |
| Time use | [time_use/](time_use/README.md) |

## Run

```bash
python evaluation/geolocation/reproduce_table.py
python evaluation/geolocation/reproduce_descriptive_table.py
python -m evaluation.event_diversity.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/event_diversity
python evaluation/interest_stats/evaluate.py
python -m evaluation.logic_consistency.evaluate --data-dir data/lifebench_10people/fenghaoran --output-dir output/logic_consistency --scores event person
python -m evaluation.time_use.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/time_use
```

Logic consistency and time use call a language model. Set credentials as described in [Configuration](../config/README.md) before running them. The geolocation table scripts and interest drift recomputation use saved inputs and run offline.

To generate interest drift reports from `data/input/` with a model, run `python -m evaluation.interest_stats.generate --dataset all`. See the [interest drift instructions](interest_stats/README.md) for options.
