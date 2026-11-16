# Event Diversity

The script counts the `type` field in top-level `daily_event.json` entries, including source and noise events. It does not recurse or filter by `atomic_id`.

## Categories

The nine official categories are `Career`, `Education`, `Relationships`, `Family&Living Situation`, `Personal Life`, `Finance`, `Health`, `Unexpected Events`, and `Other`.

Before counting, the script trims surrounding whitespace, collapses repeated whitespace, standardizes spacing around `&`, and maps aliases to official names:

| Input label | Normalized label |
| --- | --- |
| `Family & Living Situation` | `Family&Living Situation` |
| `Family &Living Situation` | `Family&Living Situation` |
| `Family` | `Family&Living Situation` |
| `Home&Living Situation` | `Family&Living Situation` |

The category count `K` uses normalized labels. Normalization does not modify `daily_event.json`.

## Output fields

- `total_events`: events with a valid `type` included in the calculation.
- `num_types`: number of normalized categories, `K`.
- `normalized_entropy`: Shannon entropy divided by `log(K)`.
- `simpson_index`: `1 - sum(p^2)`.
- `effective_num_types`: `exp(H)`.
- `type_distribution`: counts and proportions for official categories.
- `raw_type_distribution`: counts for input labels.
- `normalized_variant_events` and `normalized_variant_labels`: number of normalized events and their original labels.
- `variant_events`: event index, raw label, and normalized label for each changed event.
- `invalid_type_event_count` and `input_diagnostics`: unknown, empty, non-string, and missing `type` values. Unknown labels are not counted as an extra category or assigned to `Other`.

## Input and output paths

- `--data-dir`: a person directory or dataset directory. Each person needs `daily_event.json`.
- `<output_dir>/<person>/metrics.json`: per-person metrics and input diagnostics.
- `<output_dir>/metrics.json`: per-person results and sums of official category counts.

## Run

```bash
python -m evaluation.event_diversity.method \
  --data-dir data/lifebench_10people/fenghaoran \
  --output-dir output/event_diversity
```

For a single file:

```bash
python -m evaluation.event_diversity.method \
  --file-path data/lifebench_10people/fenghaoran/daily_event.json \
  --output-dir output/event_diversity_single
```
