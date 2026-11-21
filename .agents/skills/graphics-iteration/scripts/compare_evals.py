#!/usr/bin/env python3
"""两轮判读面结果对比：逐 leg 指标差 / 哈希 / clip，纯标准库。

配套 ag_lookdev_shading_eval.py：两份 eval_metrics.json 按 leg 名配对，输出
  - 逐 leg：base/final 的 p15/p50/p98、亮度比、clip、sha 是否相同、文件是否存在；
  - 分组汇总：body/face/hair/weapon/condition/close 的均值与最大 |Δp98|；
  - face_lr（左右 45° 对称性）四项指标的前后值；
  - checks：缺 leg、逐字节相同 leg 数、clip 非零 leg、p98 超观测上限的 leg。
只读两份 JSON，不解码 PNG（逐像素 ROI 对比是 eval 脚本的职责；整帧行为中性对拍用
acceptance-verify 的 frame_diff.py）。

用法：
  python .agents/skills/graphics-iteration/scripts/compare_evals.py <base_dir> <final_dir> \
      [--out <json>] [--p98-max 0.93]
缺省 --out = <final_dir>/compare_evals.json。退出码：0 = 已产出对比；2 = 输入缺失/不配对。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GROUPS = ("body", "face", "hair", "weapon", "condition", "close")


def load_metrics(directory: Path) -> dict:
    path = directory / "eval_metrics.json"
    if not path.is_file():
        raise SystemExit(f"[compare_evals] 缺 {path}（先跑 ag_lookdev_shading_eval.py）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[compare_evals] {path} 不是合法 JSON: {exc}") from exc
    if not isinstance(data.get("legs"), list):
        raise SystemExit(f"[compare_evals] {path} 缺 legs 数组")
    return data


def leg_map(metrics: dict) -> dict[str, dict]:
    return {leg["leg"]: leg for leg in metrics["legs"] if "leg" in leg}


def luma_of(leg: dict) -> dict:
    luma = leg.get("luma") or {}
    return {key: luma.get(key) for key in ("p15", "p50", "p98", "p98_over_p15")}


def delta(a, b):
    if a is None or b is None:
        return None
    return round(b - a, 6)


def compare(base_dir: Path, final_dir: Path, p98_max: float) -> dict:
    base = load_metrics(base_dir)
    final = load_metrics(final_dir)
    base_legs = leg_map(base)
    final_legs = leg_map(final)
    missing_in_final = sorted(set(base_legs) - set(final_legs))
    added_in_final = sorted(set(final_legs) - set(base_legs))

    legs: dict[str, dict] = {}
    per_group: dict[str, dict] = {group: {"legs": 0, "sha_same": 0,
                                          "p50": [], "p98": [], "clip": []}
                                  for group in GROUPS}
    for name in sorted(set(base_legs) & set(final_legs)):
        b, f = base_legs[name], final_legs[name]
        bl, fl = luma_of(b), luma_of(f)
        entry = {
            "group": b.get("group") or f.get("group"),
            "kind": b.get("kind"),
            "base": bl,
            "final": fl,
            "delta": {key: delta(bl[key], fl[key]) for key in ("p15", "p50", "p98")},
            "base_clip": b.get("clip_frac"),
            "final_clip": f.get("clip_frac"),
            "sha_same": b.get("sha256") == f.get("sha256"),
            "size_same": (b.get("width"), b.get("height")) == (f.get("width"), f.get("height")),
            "base_file": b.get("file"),
            "final_file": f.get("file"),
        }
        legs[name] = entry
        group = entry["group"] if entry["group"] in per_group else "body"
        bucket = per_group[group]
        bucket["legs"] += 1
        bucket["sha_same"] += int(entry["sha_same"])
        for key in ("p50", "p98"):
            if fl.get(key) is not None:
                bucket[key].append(fl[key])
        if f.get("clip_frac") is not None:
            bucket["clip"].append(f["clip_frac"])

    for bucket in per_group.values():
        for key in ("p50", "p98", "clip"):
            values = bucket[key]
            bucket[key] = round(sum(values) / len(values), 6) if values else None

    p98_over = {name: entry["final"]["p98"] for name, entry in legs.items()
                if entry["final"].get("p98") is not None and entry["final"]["p98"] > p98_max}
    clip_nonzero = {name: entry["final_clip"] for name, entry in legs.items()
                    if (entry["final_clip"] or 0) > 0}
    worst = sorted(
        ((abs(entry["delta"].get("p98") or 0.0), name) for name, entry in legs.items()),
        reverse=True)[:8]

    checks = {
        "base_legs": len(base_legs),
        "final_legs": len(final_legs),
        "compared": len(legs),
        "missing_in_final": missing_in_final,
        "added_in_final": added_in_final,
        "sha_identical": sum(1 for entry in legs.values() if entry["sha_same"]),
        "size_mismatch": [name for name, entry in legs.items() if not entry["size_same"]],
        "clip_nonzero": clip_nonzero,
        "p98_above_limit": {name: value for name, value in p98_over.items()},
        "p98_limit": p98_max,
        "worst_p98_delta": [{"leg": name, "abs_delta": round(value, 6)} for value, name in worst],
    }
    face_lr = {}
    for key in ("mean_abs_diff", "max_abs_diff", "frac_gt8", "max_abs_channel_mean_diff"):
        base_value = (base.get("face_lr") or {}).get(key)
        final_value = (final.get("face_lr") or {}).get(key)
        if base_value is not None or final_value is not None:
            face_lr[key] = {"base": base_value, "final": final_value,
                            "delta": delta(base_value, final_value)}
    return {
        "base_dir": str(base_dir),
        "final_dir": str(final_dir),
        "base_commit": base.get("commit"),
        "final_commit": final.get("commit"),
        "post": {"base": base.get("post"), "final": final.get("post")},
        "resolution": {"base": base.get("resolution"), "final": final.get("resolution")},
        "checks": checks,
        "face_lr": face_lr,
        "groups": per_group,
        "legs": legs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对比两份 ag_lookdev eval_metrics.json")
    parser.add_argument("base_dir", type=Path)
    parser.add_argument("final_dir", type=Path)
    parser.add_argument("--out", type=Path, help="输出 JSON（缺省 <final_dir>/compare_evals.json）")
    parser.add_argument("--p98-max", type=float, default=0.93,
                        help="亮部观测上限，超过即列入 p98_above_limit（缺省 0.93）")
    args = parser.parse_args(argv)
    if not args.final_dir.is_dir() or not args.base_dir.is_dir():
        raise SystemExit("[compare_evals] base/final 目录不存在")
    out = args.out or (args.final_dir / "compare_evals.json")
    report = compare(args.base_dir, args.final_dir, args.p98_max)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report["checks"], indent=2, ensure_ascii=False))
    if report["checks"]["missing_in_final"]:
        print("[compare_evals] 有 leg 缺失，先核对两轮采集口径", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
