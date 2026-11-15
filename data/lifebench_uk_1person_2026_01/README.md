# 英国人物数据

`oliver_bennett/` 包含 `persona.json`、`daily_draft.json`、`daily_event.json`、`event_tree.json` 和 `phone_data/`。可将该人物目录作为 `--data-dir` 传给支持相同输入格式的评估脚本。

例如，从仓库根目录运行事件多样性评估：

```bash
python -m evaluation.event_diversity.method --data-dir data/lifebench_uk_1person_2026_01/oliver_bennett --output-dir output/event_diversity_uk
```
