# LifeBench 评估复现包

本分支只包含五类评估：地理、事件多样性、兴趣漂移、逻辑一致性和时间使用。人物冯浩然及三种消融版本均为合成数据。`tests/`、其他评估方法、OmniBehavior 用户行为输入与本机密钥均不在分支中。

## 文件位置

| 内容 | 路径 |
|---|---|
| 五类方法代码 | `evaluation/geolocation/`、`event_diversity/`、`interest_stats/`、`logic_consistency/`、`time_use/` |
| 共用配置与调用代码 | `config/config.example.json`、`tools/`、`requirements.txt` |
| 冯浩然原始版 | `data/lifebench_10people/fenghaoran/` |
| 去规划、仅客观与模拟基线 | `data/ablation_fenghaoran_2025/` |
| 地理合成轨迹和历史地理报告 | `evaluation/geolocation/results/` |
| 其余四类已整理报告 | `results/` |

已整理结果是历史运行的静态快照。重新运行时默认写入被忽略的 `output/`，不会覆盖这些快照。五类结果的输入、窗口和复现命令见 [评估索引](evaluation/README.md)。

## 环境

```bash
pip install -r requirements.txt
cp config/config.example.json config/config.json
```

在 `config/config.json` 中配置模型密钥；需要高德服务时再配置 `geo.gaode_api_key`。也可使用 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_DEFAULT_MODEL`、`GAODE_API_KEY` 环境变量。不要提交真实配置文件。需要调用模型的方法会产生费用，且模型输出可能随服务版本变化。先使用各命令的 `--dry-run` 检查输入。

## 外部地理参考数据

地理评估以本分支中的合成轨迹 NPZ 为评估对象。WorldMove、CoPB、ChinaMobile、Tencent POI、GeoLife 等外部参考数据在本机约 13 GB；其中 ChinaMobile、Tencent、GeoLife 含真实移动轨迹，均不随本分支发布。请按各数据集的官方获取条件自行取得并运行 `evaluation/geolocation/scripts/` 下的准备脚本；`run_evaluation.py` 会检查以下相对路径：

- [WorldMove 官方仓库与下载入口](https://github.com/tsinghua-fib-lab/WorldMove)；
- [CoPB 官方仓库](https://github.com/tsinghua-fib-lab/CoPB) 提供 CoPB 合成轨迹，以及 Tencent、ChinaMobile 数据的获取说明；
- [Microsoft GeoLife 官方下载页](https://www.microsoft.com/en-us/download/details.aspx?id=52367) 提供 GeoLife 1.3。

- `evaluation/geolocation/data/WorldMove/342_CN_Beijing.npz`
- `evaluation/geolocation/data/CoPB/copb_beijing_half_hour_missing.npz`
- `evaluation/geolocation/data/WorldMove/reference_chinamobile_beijing.npz`
- `evaluation/geolocation/data/WorldMove/reference_tencent_poi_beijing.npz`

缺少上述参考数据时仍可查看已保存的地理结果，但无法从头重算该项。各外部数据集应遵守其原有许可与访问条件。

## 最小复现

在仓库根目录运行：

```bash
python -m evaluation.event_diversity.method --data-dir data/lifebench_10people/fenghaoran --output-dir output/event_diversity
python evaluation/interest_stats/run_ablation_comparison.py --dry-run
python -m evaluation.logic_consistency.legacy_two_metrics_90day --help
python evaluation/time_use/run_ablation_comparison.py --dry-run
python evaluation/geolocation/scripts/run_evaluation.py --help
```

完整运行命令、输入和既有结果索引见 [evaluation/README.md](evaluation/README.md)。

## 后续发布到匿名仓库

`anonymous-repro` 是独立根提交，不继承本仓库 `main` 的提交历史。新建匿名远端后只推送这个分支，例如 `git push <新远端名> anonymous-repro:main`；不要使用 `--all` 或 `--mirror`。发布前仍应核对新仓库的公开设置与外部数据集使用条件。
