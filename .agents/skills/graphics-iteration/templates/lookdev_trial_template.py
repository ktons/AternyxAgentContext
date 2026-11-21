#!/usr/bin/env python3
"""项目 trial 适配器模板：应用参数 → 逐 leg 出图 → 指标 → 还原 → 评分。

复制到项目脚本目录后实现 `create_session` / `snapshot` / `apply_params` /
`capture_leg` / `collect_metrics` / `restore` 六个钩子，即可接入
`param_search.py` 的评估器契约（读 `{params}`，写 `{out}/trial_score.json`）。
模板只约定流程与产物，编辑器、场景与资产接口由项目钩子实现。

参数 JSON（`--params-file` 或 `--params`）：
  {
    "volume": {"<field>": <number>, "...": <number>},
    "lights": {"<entity>": {"azimuthDeg": <number>, "elevationDeg": <number>}},
    "materials": [{"path": "<material>", "property": "<property>",
                   "index": <int>, "value": <number>}],
    "fixed": {"<name>": <value>}
  }

纪律：
  - 一次 trial 只改参数 JSON 列出的字段，结束时逐项还原并读回校验。
  - 灯光覆盖发生在 seek 之后，capture 前复读实际值；被动画写回则本 trial 失败。
  - 捕获后记录每 leg 的 PNG 哈希，同参数两次 trial 应一致；不一致时报告噪声来源。
  - 失败时写 `{"score": null, "feasible": false, "error": "..."}`，由搜索器继续。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCORE_CANDIDATES = (
    Path(__file__).with_name("score_trial.py"),
    Path(__file__).resolve().parent.parent / "scripts" / "score_trial.py",
)
DEFAULT_SCORE_SCRIPT = next((p for p in SCORE_CANDIDATES if p.is_file()), SCORE_CANDIDATES[0])


# ======================================================================================
# 项目钩子：复制后按项目编辑器/场景实现
# ======================================================================================

def create_session(args: argparse.Namespace):
    """拉起离屏会话或连接现有会话，返回项目自定义的 session 对象。"""
    raise NotImplementedError("实现 create_session：启动/连接项目编辑器会话")


def snapshot(session) -> dict:
    """记录 Volume、相机、灯光、材质等将被修改字段的当前值。"""
    raise NotImplementedError("实现 snapshot：记录将被修改字段的当前值")


def apply_params(session, params: dict) -> None:
    """应用 volume/lights/materials；未知字段直接报错，写后读回校验。"""
    raise NotImplementedError("实现 apply_params：应用参数并读回校验")


def capture_leg(session, leg: str, out_dir: Path) -> Path:
    """seek 到 leg，应用灯光覆盖，出图并返回 PNG 路径。"""
    raise NotImplementedError("实现 capture_leg：seek、覆盖灯光、出图")


def collect_metrics(session, leg: str, png: Path) -> dict:
    """返回该 leg 的指标；至少含 luma.p15/p50/p98、clip_frac，推荐带 palette/构图。"""
    raise NotImplementedError("实现 collect_metrics：调用项目的图像指标管线")


def restore(session, before: dict) -> None:
    """还原 snapshot 中的字段并读回校验。"""
    raise NotImplementedError("实现 restore：还原现场并读回校验")


# ======================================================================================
# 通用流程
# ======================================================================================

def read_params(args: argparse.Namespace) -> dict:
    if args.params_file:
        return json.loads(Path(args.params_file).read_text(encoding="utf-8"))
    if args.params:
        return json.loads(args.params)
    raise SystemExit("需要 --params-file 或 --params")


def leg_list(raw: str) -> list[str]:
    legs = [item.strip() for item in raw.split(",") if item.strip()]
    if not legs:
        raise SystemExit("--legs 为空")
    return legs


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_trial(args: argparse.Namespace) -> dict:
    params = read_params(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = create_session(args)
    before = snapshot(session)
    legs: list[dict] = []
    error: str | None = None
    try:
        apply_params(session, params)
        for leg in leg_list(args.legs):
            png = capture_leg(session, leg, out_dir)
            record = collect_metrics(session, leg, png)
            record["leg"] = leg
            record["png"] = str(png)
            record["sha256"] = sha256_file(png)
            legs.append(record)
    except Exception as exc:  # 单 trial 失败不终止搜索
        error = f"{type(exc).__name__}: {exc}"
    finally:
        restore(session, before)

    metrics = {
        "legs": legs,
        "profile": args.profile,
        "params": params,
        "error": error,
    }
    metrics_path = out_dir / "trial_metrics.json"
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    if error:
        score = {"score": None, "feasible": False, "error": error}
    else:
        score_path = out_dir / "score.json"
        subprocess.run(
            [sys.executable, str(args.score_script), "--metrics", str(metrics_path),
             "--tokens", args.tokens, "--profile", args.profile, "--out", str(score_path)],
            check=True)
        score = json.loads(score_path.read_text(encoding="utf-8"))

    (out_dir / "trial_score.json").write_text(
        json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    return score


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="项目 trial 适配器模板")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--params-file", type=Path, help="参数 JSON 文件")
    source.add_argument("--params", help="内联参数 JSON")
    parser.add_argument("--out", required=True, help="trial 输出目录")
    parser.add_argument("--legs", required=True, help="逗号分隔的 leg 列表")
    parser.add_argument("--tokens", required=True, help="design tokens 路径")
    parser.add_argument("--profile", default="day", help="token 判读 profile")
    parser.add_argument("--score-script", type=Path, default=DEFAULT_SCORE_SCRIPT,
                        help="score_trial.py 路径（复制模板后显式指定）")
    return parser.parse_args(argv)


if __name__ == "__main__":
    result = run_trial(parse_args())
    print(json.dumps(result, ensure_ascii=False))
