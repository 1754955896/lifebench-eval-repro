# Time Use Evaluation

The script reads `daily_event.json` in a person directory and calls a model to classify activities as sleep, work, study, daily living, leisure, or other. An optional `persona.json` provides additional person information.

## Run

From the repository root:

```bash
python -m evaluation.time_use.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/time_use
```

Use `--dry-run` to check inputs, `--start-date` and `--end-date` to limit dates, and `--sleep-policy none|night-gap` to choose whether to estimate sleep from overnight gaps between events. Run `--help` for other options.

The output directory's `metrics.json` contains daily average duration, average duration on participating days, participation rate, variance, percentage, and event count for each category. `run_ablation_comparison.py` requires separate simulation baseline data passed with `--simulation-baseline-dir`.
