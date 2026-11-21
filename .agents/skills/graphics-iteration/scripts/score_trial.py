#!/usr/bin/env python3
"""复合评分：token 窗口 → 逐 leg 罚分 → 搜索目标 / 判读报告。

定位：图形迭代「闭环参数搜索」的评分母本（对应 references/08-search.md）。输入是
评估脚本或项目 trial 适配器产出的逐 leg 指标 + design tokens；输出是单一标量
（0 = 全部落在 token 窗口内）与可解释的分解，供 CMA-ES/TPE 使用，也可
单独作为一轮迭代的量化判读。

设计纪律：
  - 窗口/权重只来自设计 token（`lookdev.window.*` / `lookdev.weight.*`），艺术阈值全部
    由 token 给出；token 缺失的指标只观察、不计分（在 `observed` 里列出）。
  - 打分 = 加权 mean + 加权 max 的混合：mean 拉整体、max 防「平均好但某机位坏」。
  - 硬门（feasible=false）覆盖「不可能被接受」的情况：缺 leg、clip 超硬上限、编辑器
    ERROR、参数未还原；窗口偏离是软罚分，艺术目标由此获得可搜索的连续地形。
  - 画质模型（MUSIQ/CLIP-IQA）如提供，只作参考项并单独列出，`score=0` 由窗口指标决定。

用法：
  python .../score_trial.py --metrics temp/<任务>/eval_metrics.json \
      --tokens <tokens.json> \
      --profile day --out temp/<任务>/score.json

  # 自检（窗口内 0 分、越界单调、缺 leg 硬门）
  python .../score_trial.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from design_tokens import load_tokens, weight_for, window_for  # noqa: E402

SCORE_VERSION = 1
DEFAULT_HARD_CLIP = 0.01          # clip 像素占比硬上限（超过即不可接受）
DEFAULT_SCALE = {                 # 单侧窗口缺失时的归一化尺度（不是阈值）
    "p15": 0.08, "p50": 0.08, "p98": 0.08, "ratio": 0.8, "clip": 0.01,
    "shadowCoverage": 0.10, "paletteCoverage": 0.10, "paletteDeltaE": 4.0,
    "subjectHeight": 0.05, "subjectWidth": 0.05, "centerOffset": 0.05,
    "thirdsDistance": 0.05, "headroom": 0.03,
}

# token 指标名 → (取值函数, 权重名)
METRIC_ACCESSORS = {
    "p15": ("p15", lambda leg: _get(leg, "luma", "p15")),
    "p50": ("p50", lambda leg: _get(leg, "luma", "p50")),
    "p98": ("p98", lambda leg: _get(leg, "luma", "p98")),
    "ratio": ("ratio", lambda leg: _get(leg, "luma", "p98_over_p15")),
    "clip": ("clip", lambda leg: leg.get("clip_frac")),
    "shadowCoverage": ("shadowCoverage", lambda leg: leg.get("shadow_coverage")),
    "paletteCoverage": ("paletteCoverage", lambda leg: _get(leg, "palette", "coverage")),
    "paletteDeltaE": ("paletteDeltaE", lambda leg: _get(leg, "palette", "mean_delta_e")),
    "subjectHeight": ("subjectHeight",
                      lambda leg: _get(leg, "subject_composition", "height_frac")),
    "subjectWidth": ("subjectWidth",
                     lambda leg: _get(leg, "subject_composition", "width_frac")),
    "centerOffset": ("centerOffset",
                     lambda leg: _get(leg, "saliency_composition", "center_offset")),
    "thirdsDistance": ("thirdsDistance",
                       lambda leg: _get(leg, "saliency_composition", "thirds_distance")),
    "headroom": ("headroom", lambda leg: _get(leg, "saliency_composition", "headroom_frac")),
}
PAIR_METRICS = {"p15": "p15", "p50": "p50", "p98": "p98", "ratio": "ratio",
                "clip": "clip", "shadowCoverage": "shadowCoverage",
                "paletteCoverage": "paletteCoverage", "paletteDeltaE": "paletteDeltaE",
                "subjectHeight": "subjectHeight", "subjectWidth": "subjectWidth",
                "centerOffset": "centerOffset", "thirdsDistance": "thirdsDistance",
                "headroom": "headroom"}


def _get(leg: dict, section: str, key: str):
    value = leg.get(section)
    return value.get(key) if isinstance(value, dict) else None


def _window_pair(group_windows: dict, metric: str) -> tuple[float | None, float | None]:
    return group_windows.get(f"{metric}Min"), group_windows.get(f"{metric}Max")


def _penalty(value: float, low: float | None, high: float | None, scale: float) -> float:
    if low is not None and value < low:
        return (low - value) / max(scale, 1e-9)
    if high is not None and value > high:
        return (value - high) / max(scale, 1e-9)
    return 0.0


def enrich_with_images(metrics: dict, tokens: dict, image_dir: Path) -> None:
    """给 eval_metrics 的 leg 补 palette/构图（评估脚本未提供这两类时补齐）。"""
    from image_metrics import analyze_file
    if not tokens.get("palette"):
        raise SystemExit("--images 需要 token 文件含 color.palette.*")
    for leg in metrics.get("legs") or []:
        file_name = leg.get("file")
        if not file_name:
            continue
        path = image_dir / file_name
        if not path.is_file():
            raise SystemExit(f"--images: 缺 {path}")
        group = leg.get("group") or "body"
        roi = "face" if group == "face" else "torso"
        detail = analyze_file(path, tokens["palette"], roi, "nonsky", 2,
                              tokens.get("palette_threshold", 10.0))
        leg["palette"] = detail.get("palette")
        leg["color"] = detail.get("color")
        full = analyze_file(path, None, "full", "nonsky", 4)
        leg["subject_composition"] = full.get("subject_composition")
        leg["saliency_composition"] = full.get("saliency_composition")
        leg["subject_bbox"] = full.get("subject_bbox")
        leg["saliency"] = full.get("saliency")


def score(metrics: dict, tokens: dict, profile: str = "day",
          required_legs: list[str] | None = None,
          hard_clip: float = DEFAULT_HARD_CLIP,
          quality: dict | None = None) -> dict:
    legs = metrics.get("legs") or []
    if not legs:
        raise SystemExit("[score_trial] metrics 缺 legs[]")
    leg_names = [leg.get("leg") for leg in legs]
    missing = sorted(set(required_legs or []) - set(leg_names))
    hard: list[str] = []
    if missing:
        hard.append(f"缺 leg: {missing}")
    editor_errors = (metrics.get("editor") or {}).get("error_count")
    if editor_errors:
        hard.append(f"编辑器 ERROR {editor_errors} 条")

    if not any(window_for(tokens, profile, group) for group in
               ("body", "face", "hair", "weapon", "condition", "close")):
        raise SystemExit(f"token 没有 profile '{profile}' 的任何窗口；先补 lookdev.window.{profile}.*")

    per_leg: dict[str, dict] = {}
    penalties: list[float] = []
    observed: dict[str, dict] = {}
    clip_violations = []
    for leg in legs:
        name = leg.get("leg") or "?"
        group = leg.get("group") or "body"
        windows = window_for(tokens, profile, group)
        if not windows and group != "body":
            windows = window_for(tokens, profile, "body")
        entry: dict = {"group": group, "terms": {}, "penalty": 0.0,
                       "clip_frac": leg.get("clip_frac")}
        total = 0.0
        weight_sum = 0.0
        for metric, (weight_name, accessor) in METRIC_ACCESSORS.items():
            value = accessor(leg)
            low, high = _window_pair(windows, metric)
            if low is None and high is None:
                if value is not None:
                    observed.setdefault(metric, {})[name] = value
                continue
            if value is None:
                entry["terms"][metric] = {"window": [low, high], "value": None,
                                          "penalty": None}
                continue
            scale = (high - low) if (low is not None and high is not None) \
                else DEFAULT_SCALE.get(metric, 0.05)
            distance = _penalty(value, low, high, scale)
            term_weight = weight_for(tokens, group, weight_name)
            entry["terms"][metric] = {"window": [low, high], "value": round(value, 6),
                                      "scale": round(scale, 6),
                                      "penalty": round(distance, 6),
                                      "weight": term_weight}
            total += term_weight * distance
            weight_sum += term_weight
            if distance > 0:
                penalties.append(distance)
        entry["penalty"] = round(total / weight_sum, 6) if weight_sum else 0.0
        per_leg[name] = entry
        clip_value = leg.get("clip_frac")
        if clip_value is not None and clip_value > hard_clip:
            clip_violations.append({"leg": name, "clip_frac": clip_value})
    if clip_violations:
        hard.append(f"clip 超硬上限 {hard_clip}: {clip_violations[:3]}")

    mean_penalty = (sum(entry["penalty"] for entry in per_leg.values())
                    / len(per_leg)) if per_leg else 0.0
    worst = max((entry["penalty"] for entry in per_leg.values()), default=0.0)
    # 混合目标：mean 拉整体、max 防单机位崩坏（max 权重 0.35）
    objective = 0.65 * mean_penalty + 0.35 * worst

    quality_report = None
    if quality:
        quality_report = evaluate_quality(quality, metrics)
        if quality_report.get("penalty"):
            objective += quality_report["penalty"]

    return {
        "score_version": SCORE_VERSION,
        "metrics_file": metrics.get("file"),
        "tokens_file": tokens.get("file"),
        "profile": profile,
        "feasible": not hard,
        "hard_failures": hard,
        "score": round(objective, 6),
        "components": {
            "mean_penalty": round(mean_penalty, 6),
            "worst_leg_penalty": round(worst, 6),
            "penalized_terms": len(penalties),
        },
        "per_leg": per_leg,
        "worst_legs": [{"leg": name, "penalty": entry["penalty"]}
                       for name, entry in sorted(per_leg.items(),
                                                 key=lambda item: item[1]["penalty"],
                                                 reverse=True)[:5]],
        "observed_only": {metric: values for metric, values in observed.items()},
        "warnings": ([f"非预期 git 改动: {metrics['git_status_unexpected'][:3]}"]
                     if metrics.get("git_status_unexpected") else []),
        "quality": quality_report,
        "note": "0 = 全部 token 窗口内；硬门见 hard_failures；observed_only 表示 token 未给窗口",
    }


def evaluate_quality(quality: dict, metrics: dict) -> dict:
    """画质模型参考项：相对基线的下降超过容差才产生罚分；硬门看窗口指标。

    输入形如：
      {"model": "musiq", "baseline": {"front_front": 62.1}, "tolerance": 1.5,
       "scores": {"front_front": 60.9}}
    或 {"legs": {"front_front": {"musiq": 60.9}}, "baseline": {...}}
    """
    baseline = quality.get("baseline") or {}
    scores = quality.get("scores")
    if scores is None:
        scores = {name: values.get(quality.get("model", "score"))
                  for name, values in (quality.get("legs") or {}).items()}
    tolerance = float(quality.get("tolerance", 0.0))
    deltas = {}
    penalty = 0.0
    for leg, value in scores.items():
        base = baseline.get(leg)
        if base is None or value is None:
            continue
        delta = float(value) - float(base)
        deltas[leg] = round(delta, 4)
        if delta < -tolerance:
            penalty += (-delta - tolerance) / max(tolerance, 1.0)
    return {"model": quality.get("model"), "tolerance": tolerance,
            "deltas": deltas, "penalty": round(penalty, 6),
            "note": "参考项；无基线/无模型时为空，不作为效果门"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="token 驱动的复合评分")
    parser.add_argument("--metrics", type=Path)
    parser.add_argument("--tokens", type=Path)
    parser.add_argument("--profile", default="day", help="lookdev.window.<profile>（缺省 day）")
    parser.add_argument("--require-leg", action="append", default=[],
                        help="必须存在的 leg（可多次）；缺一即硬门")
    parser.add_argument("--hard-clip", type=float, default=DEFAULT_HARD_CLIP)
    parser.add_argument("--quality", type=Path, help="画质模型分数 JSON（参考项）")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--images", type=Path,
                        help="eval_metrics 同目录/PNG 目录：补 palette 与构图指标（可选）")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def _self_test() -> int:
    tokens = {
        "file": "<self-test>",
        "palette": [],
        "windows": {"day": {"body": {"p98Min": 0.82, "p98Max": 0.93,
                                      "p15Min": 0.18, "p15Max": 0.35,
                                      "ratioMin": 2.5, "ratioMax": 4.0, "clipMax": 0.0},
                             "face": {"p98Min": 0.82, "p98Max": 0.93}}},
        "weights": {"body": {"p98": 2.0, "clip": 4.0}},
    }
    base = {
        "legs": [
            {"leg": "front_front", "group": "body",
             "luma": {"p15": 0.25, "p50": 0.55, "p98": 0.88, "p98_over_p15": 3.5},
             "clip_frac": 0.0},
            {"leg": "face_front", "group": "face",
             "luma": {"p15": 0.3, "p50": 0.55, "p98": 0.87, "p98_over_p15": 2.9},
             "clip_frac": 0.0},
        ],
        "editor": {"error_count": 0},
    }
    problems = []
    ok = score(base, tokens)
    if ok["score"] != 0.0 or not ok["feasible"]:
        problems.append(f"窗口内应 0 分: {ok['score']} {ok['hard_failures']}")

    worse = json.loads(json.dumps(base))
    worse["legs"][0]["luma"]["p98"] = 0.98
    high = score(worse, tokens)
    if high["score"] <= 0:
        problems.append("p98 越上限罚分未生效")
    worse["legs"][0]["luma"]["p98"] = 0.95
    mid = score(worse, tokens)
    if not (0 < mid["score"] < high["score"]):
        problems.append(f"越界罚分应随偏移单调: {mid['score']} vs {high['score']}")

    clipped = json.loads(json.dumps(base))
    clipped["legs"][0]["clip_frac"] = 0.02
    clip_score = score(clipped, tokens)
    if clip_score["feasible"]:
        problems.append("clip 硬门未生效")

    missing = score(base, tokens, required_legs=["back180_back"])
    if missing["feasible"]:
        problems.append("缺 leg 硬门未生效")

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1
    print("[OK] score_trial self-test: 0 分/单调罚分/clip 硬门/缺 leg 全过")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.metrics or not args.tokens:
        raise SystemExit("需要 --metrics 与 --tokens")
    metrics = json.loads(args.metrics.read_text(encoding="utf-8-sig"))
    metrics["file"] = str(args.metrics)
    tokens = load_tokens(args.tokens)
    if args.images:
        enrich_with_images(metrics, tokens, args.images)
    quality = json.loads(args.quality.read_text(encoding="utf-8-sig")) if args.quality else None
    result = score(metrics, tokens, args.profile, args.require_leg, args.hard_clip, quality)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
