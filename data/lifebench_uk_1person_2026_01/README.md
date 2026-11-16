# UK Person Data

`oliver_bennett/` contains `persona.json`, `daily_draft.json`, `daily_event.json`, `event_tree.json`, and `phone_data/`. Pass this person directory as `--data-dir` to a compatible [evaluation script](../../evaluation/README.md).

For example, run event diversity evaluation from the repository root:

```bash
python -m evaluation.event_diversity.method --data-dir data/lifebench_uk_1person_2026_01/oliver_bennett --output-dir output/event_diversity_uk
```
