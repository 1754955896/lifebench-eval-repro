# Logic Consistency Evaluation

The entry point is `evaluate.py`. Run it from the repository root. A person directory needs `daily_event.json` and `persona.json`; `relation_update` also needs `event_tree.json`.

```bash
python -m evaluation.logic_consistency.evaluate --data-dir data/lifebench_10people/fenghaoran --output-dir output/logic_consistency --scores event person
```

Pass one or more of `event`, `person`, `location`, `path`, and `relation_update` to `--scores`. The default selects the first four. The final score is the arithmetic mean of the selected scores; it is `null` if any selected score is unavailable.

| Score | Calculation |
| --- | --- |
| `event` | correct events / (correct events + incorrect events) |
| `person` | correct person relations / (correct person relations + incorrect person relations) |
| `location` | successful location checks / (successful checks + suspected errors) |
| `path` | (failed route queries + successful route queries - route time errors) / (successful + failed route queries) |
| `relation_update` | conform / (conform + nonconform) |

Use `--start-date` and `--end-date` to limit dates, `--persons` to select people in a dataset, and `--dry-run` to check inputs without model or map calls. Run `--help` for all options.

The implementations are under `code/event/`, `code/person/`, `code/location/`, `code/path/`, and `code/relation_update/`. Per-person and aggregate results are written to `metrics.json` in the output directory.
