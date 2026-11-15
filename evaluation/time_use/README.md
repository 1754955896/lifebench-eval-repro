# 时间使用评估

脚本读取人物目录中的 `daily_event.json`，调用模型将活动归入睡眠、工作、学习、生活、休闲和其他六类，再统计各类时间使用情况。人物目录也可包含 `persona.json`，用于补充人物信息。

## 运行

从仓库根目录执行：

```bash
python -m evaluation.time_use.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/time_use
```

可添加 `--dry-run` 检查输入；通过 `--start-date`、`--end-date` 限定日期，通过 `--sleep-policy none|night-gap` 选择是否按夜间事件间隔推算睡眠。使用 `--help` 查看其余参数。

脚本将各类活动的每日平均时长、参与日平均时长、参与率、方差、占比和事件数写入输出目录。`run_ablation_comparison.py` 需要另外提供模拟基线数据；运行时使用 `--simulation-baseline-dir` 指向该数据目录。
