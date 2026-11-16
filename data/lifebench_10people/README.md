# LifeBench Person Data

Each person has a subdirectory. Evaluation scripts read its `daily_event.json`; methods that need person information or relation events also read `persona.json` or `event_tree.json`.

Run event diversity evaluation for one person from the repository root:

```bash
python -m evaluation.event_diversity.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/event_diversity
```

To evaluate the full dataset, set `--data-dir` to `data/lifebench_10people`.
