# LifeBench 评估复现包

本仓库提供地理、事件多样性、兴趣漂移、逻辑一致性和时间使用评估的输入数据与运行脚本。

## 文件位置

| 内容 | 路径 |
|---|---|
| 五类方法代码 | `evaluation/geolocation/`、`event_diversity/`、`interest_stats/`、`logic_consistency/`、`time_use/` |
| 共用配置与调用代码 | `config/config.example.json`、`tools/`、`requirements.txt` |
| LifeBench 10 人数据 | `data/lifebench_10people/` |
| 北京 5 人数据 | `data/lifebench_5p_beijing/` |
| 去规划、仅客观与仅主观数据 | `data/ablation_fenghaoran_2025/` |
| 英国人物数据 | `data/lifebench_uk_1person_2026_01/` |
| LifeBench 仓库提交历史 | `git log/HISTORY.md` |
| 地理轨迹输入及表格 | `evaluation/geolocation/data/`、`evaluation/geolocation/table.json`、`evaluation/geolocation/descriptive_table.json` |
| 兴趣漂移输入及保存结果 | `evaluation/interest_stats/data/` |

各方法的输入和运行命令见 [评估索引](evaluation/README.md)。运行输出可写入被忽略的 `output/`。

`git log/HISTORY.md` 保存 LifeBench 仓库的提交历史。

## 环境

```bash
pip install -r requirements.txt
cp config/config.example.json config/config.json
```

在 `config/config.json` 中配置模型密钥；需要高德服务时再配置 `geo.gaode_api_key`。也可使用 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_DEFAULT_MODEL`、`GAODE_API_KEY` 环境变量。不要提交真实配置文件。

## 最小复现

在仓库根目录运行：

```bash
python -m evaluation.event_diversity.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/event_diversity
python evaluation/interest_stats/evaluate.py
python -m evaluation.logic_consistency.evaluate --data-dir data/lifebench_10people/fenghaoran --output-dir output/logic_consistency --scores event person
python -m evaluation.time_use.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/time_use
python evaluation/geolocation/reproduce_table.py
python evaluation/geolocation/reproduce_descriptive_table.py
```

完整命令和输入说明见 [evaluation/README.md](evaluation/README.md)。
