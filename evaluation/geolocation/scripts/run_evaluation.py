"""一键运行 data_new 的北京轨迹评估。

默认依次运行两项评估，每项都同时包含 WorldMove 与 CoPB 作为对照：

1. data_new vs ChinaMobile 参照（reference_chinamobile_beijing.npz）
2. data_new vs Tencent POI 参照（reference_tencent_poi_beijing.npz）

用法（任意目录均可运行）：

    python evaluation/geolocation/scripts/run_evaluation.py
    python evaluation/geolocation/scripts/run_evaluation.py --sample-size 242
    python evaluation/geolocation/scripts/run_evaluation.py --references chinamobile
    python evaluation/geolocation/scripts/run_evaluation.py --dry-run

输出写入 evaluation/geolocation/results/，文件名形如
lifebench_data_new_vs_chinamobile.json。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[2]  # scripts -> geolocation -> evaluation -> lifebench_data_eval
GEO_DIR = REPO_ROOT / "evaluation" / "geolocation"

DEFAULT_LIFEBENCH = GEO_DIR / "results" / "lifebench_5p_300d_new.npz"
DEFAULT_WORLDMOVE = GEO_DIR / "data" / "WorldMove" / "342_CN_Beijing.npz"
DEFAULT_COPB = GEO_DIR / "data" / "CoPB" / "copb_beijing_half_hour_missing.npz"
DEFAULT_OUTPUT_DIR = GEO_DIR / "results"

REFERENCES = {
    "chinamobile": GEO_DIR / "data" / "WorldMove" / "reference_chinamobile_beijing.npz",
    "tencent_poi": GEO_DIR / "data" / "WorldMove" / "reference_tencent_poi_beijing.npz",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行 data_new 对 ChinaMobile / Tencent POI 的评估（WorldMove、CoPB 作为对照）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--lifebench", type=Path, default=DEFAULT_LIFEBENCH,
                        help="data_new 导出的 (*, 48) NPZ")
    parser.add_argument("--worldmove", type=Path, default=DEFAULT_WORLDMOVE,
                        help="WorldMove 北京网格 NPZ")
    parser.add_argument("--copb", type=Path, default=DEFAULT_COPB,
                        help="CoPB 半小时网格 NPZ（缺测保留）")
    parser.add_argument("--references", nargs="+", default=["chinamobile", "tencent_poi"],
                        choices=sorted(REFERENCES),
                        help="要评估的真实参照，可多选")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--tag", default="data_new", help="输出文件名中的版本标识")
    parser.add_argument("--sample-size", type=int, default=300,
                        help="每次重复抽样的日轨迹数；data_new 不足时按实际数量")
    parser.add_argument("--repeats", type=int, default=50, help="重复抽样次数")
    parser.add_argument("--seed-start", type=int, default=100, help="起始随机种子")
    parser.add_argument("--dry-run", action="store_true", help="只打印命令，不执行")
    return parser.parse_args()


def check_inputs(args: argparse.Namespace) -> None:
    required = {
        "LifeBench NPZ": args.lifebench,
        "WorldMove NPZ": args.worldmove,
        "CoPB NPZ": args.copb,
    }
    for label, path in required.items():
        if not path.exists():
            raise SystemExit(f"缺少{label}：{path}")
    for name in args.references:
        if not REFERENCES[name].exists():
            raise SystemExit(f"缺少 {name} 参照：{REFERENCES[name]}")


def build_command(args: argparse.Namespace, name: str, output: Path) -> list[str]:
    return [
        sys.executable,
        str(SCRIPTS_DIR / "evaluate_lifebench.py"),
        "--lifebench", str(args.lifebench),
        "--worldmove", str(args.worldmove),
        "--reference", str(REFERENCES[name]),
        "--reference-name", name,
        "--copb", str(args.copb),
        "--output", str(output),
        "--sample-size", str(args.sample_size),
        "--repeats", str(args.repeats),
        "--seed-start", str(args.seed_start),
        "--lifebench-min-observed-slots", "48",
        "--copb-min-observed-slots", "4",
    ]


def main() -> None:
    args = parse_args()
    check_inputs(args)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    suffix = "" if args.sample_size == 300 else f"_n{args.sample_size}"
    print(f"LifeBench: {args.lifebench}")
    print(f"WorldMove: {args.worldmove}")
    print(f"CoPB:      {args.copb}")
    print(f"抽样:      最多 {args.sample_size} 条/次, 重复 {args.repeats} 次, 种子 "
          f"{args.seed_start}-{args.seed_start + args.repeats - 1}")
    print()

    for name in args.references:
        output = args.output_dir / f"lifebench_{args.tag}_vs_{name}{suffix}.json"
        command = build_command(args, name, output)
        print(f"[{name}] -> {output}")
        if args.dry_run:
            print("  " + " ".join(command))
            continue
        subprocess.run(command, check=True)
        print(f"  完成: {output}\n")


if __name__ == "__main__":
    main()
