# Interest Drift Evaluation

## Recompute from saved classifications

Run from the repository root:

```bash
python evaluation/interest_stats/evaluate.py
```

By default, the script reads `evaluation/interest_stats/data/manifest.json`. Its `normalized_input` and `coarse_report` paths are relative to `data/`: the first file provides event records, and the second provides per-event interest categories in `event_extractions`. The script joins them by `event_id`, recomputes interest drift, and checks the saved metrics. It requires neither a model nor third-party packages.

To use another directory with the same layout or save recomputation details:

```bash
python evaluation/interest_stats/evaluate.py --data-dir PATH_TO_DATA
python evaluation/interest_stats/evaluate.py --output output/interest_stats/recomputed.json
```

Each record in `data/input/<dataset>/<sample>.json` needs at least `event_id` and `date`. The corresponding `data/output/<dataset>/<sample>.json` needs `event_extractions`, `provenance`, `daily_profiles`, `coverage`, and `interest_drift`. The manifest lists both files and `event_count` for each sample.

For each day, the script collects unique categories with `interest_signal=true`. It compares consecutive calendar days using `1 - |intersection| / |union|`. It skips pairs where both sets are empty and assigns a drift of 1 when only one set is empty. Daily drift is rounded to six decimal places before per-person and dataset summaries are calculated.

## Generate reports from inputs

`generate.py` reads normalized events in `data/input/`, calls a model to classify interests, and computes the same drift metrics. It uses dataset-specific extraction prompts for `lifebench`, `mempal`, and `omni`. Set `llm.api_key` as described in [Configuration](../../config/README.md), or set `LLM_API_KEY` or `DEEPSEEK_API_KEY`.

```bash
python -m evaluation.interest_stats.generate --dataset all --dry-run
python -m evaluation.interest_stats.generate --dataset all
```

To process one input:

```bash
python -m evaluation.interest_stats.generate --dataset mempal --sample 0000
```

New reports go to `output/interest_stats/online/<dataset>/<sample>.json` by default, with cache files in `output/interest_stats/cache/`. Run `--help` for batch size, concurrency, model, and output options. Model-generated classifications and metrics may differ from the saved reports; use the offline command above to verify the saved metrics.
