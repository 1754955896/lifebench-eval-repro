# event_diversity — 事件类型多样性

统计顶层 `daily_event.json` 的 `type` 字段，包括来源事件和噪声事件；
不递归、不按 `atomic_id` 筛选。

## 官方九类

`Career`、`Education`、`Relationships`、`Family&Living Situation`、
`Personal Life`、`Finance`、`Health`、`Unexpected Events`、`Other`。

## 计数前归一化

```text
去首尾空白 → 折叠连续空白 → 统一 & 两侧空格 → 官方/别名表映射
```

已确认别名：

| 原始字符串 | 规范结果 |
|---|---|
| `Family & Living Situation` | `Family&Living Situation` |
| `Family &Living Situation` | `Family&Living Situation` |
| `Family` | `Family&Living Situation` |
| `Home&Living Situation` | `Family&Living Situation` |

`K` 按归一化后的类别计数获得，不再使用归一化前的字符串数量。官方九类
输入保持不变，函数幂等；映射只作用于计算，不写回 `daily_event.json`。

## 指标

- `total_events`：参与计算的有效 type 事件数。
- `num_types`：归一化后类别数 K。
- `normalized_entropy`：Shannon 熵 / log(K)。
- `simpson_index`：1 − Σp²。
- `effective_num_types`：exp(H)。
- `type_distribution`：官方类别计数与比例。
- `raw_type_distribution`：原始字符串频数（核查用）。
- `normalized_variant_events` / `normalized_variant_labels`：被规范化的
  事件数与原始标签集合。
- `variant_events`：逐条审计（`event_index`、`raw`、`normalized`），
  保留原始事件索引，避免删除无效标签后错位。
- `invalid_type_event_count` / `input_diagnostics`：未知、空、非字符串与
  缺失 type 的显式诊断；不把未知标签计作第十类、不默认塞进 `Other`、
  不静默删除。

## 输入与输出

- `--data-dir`：单人物目录或数据集目录，人物需含 `daily_event.json`。
- `<output_dir>/<person>/metrics.json`：逐人物指标与输入诊断。
- `<output_dir>/metrics.json`：逐人物计数与官方类别计数总和（不平均比例）。

## 运行

```bash
python -m evaluation.event_diversity.method \
  --data-dir data/lifebench_10people/fenghaoran \
  --output-dir output/event_diversity

# 兼容原单文件入口
python -m evaluation.event_diversity.method \
  --file-path data/lifebench_10people/fenghaoran/daily_event.json \
  --output-dir output/event_diversity_single
```

可选真实数据验收：对本地十人数据统计原始标签数、规范化变体数与事件
总数；这些数值由输入实际统计，不写死为固定结果。
