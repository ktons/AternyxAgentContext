#!/usr/bin/env python3
"""裸帧 A/B 差异掩膜与量化（开关对照/光向摆动/作用域判定）。

base 帧 + 若干 overlay 帧逐像素对比，产出：
  1) 合成掩膜 PNG（--mask-out）：灰底，每个 overlay 的差异像素按分配色着色
     （绿/品红/青/黄循环，后写覆盖先写）——多足迹重叠度肉眼判读（两组足迹迁移
     至不相交 = 方向响应正确）；
  2) 量化 JSON（stdout；--json <path> 同时落盘）：每 overlay 差异像素计数、
     bbox、质心（差异像素均值坐标）——作用域判定（bbox ⊆ 特征区）与方向响应
     （质心随输入迁移）的数字证据；
  3) --runs-y Y --runs-thr T：对 base 指定扫描线输出 像素亮度<=T 的游程列表与
     众数（带宽测量：描边/高光带像素宽度，逐特性定阈值）。

帧尺寸取 base 同名 meta.json 或 --width/--height；所有帧尺寸必须一致。
差异判定 = 任通道 |a-b| > --thr（缺省 0 = 逐字节不同）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

MASK_COLORS = [(0, 255, 0), (255, 0, 255), (0, 255, 255), (255, 255, 0)]


def log(msg: str) -> None:
    print(f"[frame_diff_mask] {msg}", flush=True)


def load_frame(bin_path: pathlib.Path, args) -> bytes:
    if not bin_path.exists():
        log(f"ERROR: {bin_path} 不存在")
        sys.exit(2)
    px = bin_path.read_bytes()
    if len(px) < args.width * args.height * 4:
        log(f"ERROR: {bin_path}: {len(px)}B < {args.width}x{args.height}x4")
        sys.exit(2)
    return px[:args.width * args.height * 4]


def resolve_size(bin_path: pathlib.Path, args) -> tuple[int, int]:
    if args.width and args.height:
        return args.width, args.height
    meta = bin_path.parent / (bin_path.stem.rsplit("_frame", 1)[0] + "_meta.json")
    if meta.exists():
        m = json.loads(meta.read_text())
        if m.get("width") and m.get("height"):
            return int(m["width"]), int(m["height"])
    log(f"ERROR: {bin_path}: 无同名 meta.json 且未给 --width/--height")
    sys.exit(2)


def diff_stats(base: bytes, overlay: bytes, width: int, height: int, thr: int) -> dict:
    count = 0
    xs = [width, 0]
    ys = [height, 0]
    sx = sy = 0
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            i = row + x * 4
            if (abs(base[i] - overlay[i]) > thr or abs(base[i + 1] - overlay[i + 1]) > thr
                    or abs(base[i + 2] - overlay[i + 2]) > thr):
                count += 1
                xs = [min(xs[0], x), max(xs[1], x)]
                ys = [min(ys[0], y), max(ys[1], y)]
                sx += x
                sy += y
    return {
        "diff_px": count,
        "bbox": None if count == 0 else [xs[0], ys[0], xs[1], ys[1]],
        "centroid": None if count == 0 else [round(sx / count, 1), round(sy / count, 1)],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", help="基准帧 .bin（对照组/关断组）")
    parser.add_argument("overlays", nargs="+", help="对照帧 .bin（特性组/摆动组，逐个对 base 比）")
    parser.add_argument("--mask-out", help="合成掩膜 PNG 输出路径（缺省不出图）")
    parser.add_argument("--json", help="量化 JSON 落盘路径（stdout 恒输出）")
    parser.add_argument("--thr", type=int, default=0, help="通道差阈值（缺省 0 = 逐字节）")
    parser.add_argument("--runs-y", type=int, help="对 base 指定扫描线做游程测量")
    parser.add_argument("--runs-thr", type=int, default=14, help="游程判定亮度阈值（缺省 14，特征相关）")
    parser.add_argument("--width", type=int, help="显式帧宽（无 meta 时必给）")
    parser.add_argument("--height", type=int, help="显式帧高（无 meta 时必给）")
    args = parser.parse_args()

    base_path = pathlib.Path(args.base)
    width, height = resolve_size(base_path, args)
    args.width, args.height = width, height
    base = load_frame(base_path, args)
    overlays = [(pathlib.Path(o), load_frame(pathlib.Path(o), args)) for o in args.overlays]

    report: dict = {"base": str(base_path), "size": [width, height], "thr": args.thr,
                    "overlays": {}}
    mask = None
    if args.mask_out:
        gray = 60
        mask = bytearray(bytes([gray, gray, gray, 255]) * width * height)

    for index, (path, px) in enumerate(overlays):
        stats = diff_stats(base, px, width, height, args.thr)
        report["overlays"][str(path)] = stats
        log(f"{path.name}: diff_px={stats['diff_px']} bbox={stats['bbox']} centroid={stats['centroid']}")
        if mask is not None and stats["diff_px"]:
            r, g, b = MASK_COLORS[index % len(MASK_COLORS)]
            for y in range(height):
                row = y * width * 4
                for x in range(width):
                    i = row + x * 4
                    if (abs(base[i] - px[i]) > args.thr or abs(base[i + 1] - px[i + 1]) > args.thr
                            or abs(base[i + 2] - px[i + 2]) > args.thr):
                        j = i
                        mask[j], mask[j + 1], mask[j + 2] = r, g, b

    if args.runs_y is not None:
        y = args.runs_y
        runs, cur = [], 0
        row = y * width * 4
        for x in range(width):
            i = row + x * 4
            lum = (base[i] + base[i + 1] + base[i + 2]) // 3
            if lum <= args.runs_thr:
                cur += 1
            elif cur:
                runs.append(cur)
                cur = 0
        if cur:
            runs.append(cur)
        mode = max(set(runs), key=runs.count) if runs else None
        report["runs"] = {"y": y, "thr": args.runs_thr, "lengths": runs, "mode": mode}
        log(f"runs@y={y} thr={args.runs_thr}: {runs} 众数={mode}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    if args.mask_out:
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        from frame_to_png import write_png
        out = pathlib.Path(args.mask_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_png(out, bytes(mask), width, height)
        log(f"mask -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
