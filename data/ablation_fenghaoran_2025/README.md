# Ablation Data

`no_planning/`, `objective_only/`, and `subjective_only/` each contain a `fenghaoran/` person directory. Pass the desired person directory as `--data-dir`.

For example, run the event diversity evaluation from the repository root:

```bash
python -m evaluation.event_diversity.method --data-dir data/ablation_fenghaoran_2025/subjective_only/fenghaoran --output-dir output/event_diversity_subjective
```

See the [evaluation instructions](../../evaluation/README.md) for the files required by other methods.
