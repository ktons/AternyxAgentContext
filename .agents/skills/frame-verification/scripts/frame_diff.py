"""4 字节/像素裸帧逐字节对比：单文件，或按 seq 配对的目录。

两种模式:
    python3 frame_diff.py <a.bin> <b.bin>              # 单文件：同尺寸同格式, 4字节/像素
    python3 frame_diff.py <dir_a> <dir_b> [--skip-seq 0,7]   # 目录：按 seq 配对 <prefix>_frame<seq>.bin

单文件输出: 最大通道差 / 非零差像素数 / 差值分布；完全一致退出码 0, 否则 1。
目录模式对公共 seq 逐帧判定并给汇总行（N/M IDENTICAL），--skip-seq 只把指定 seq 标记为
SKIPPED（豁免意图标注），判读责任仍在使用方：DIFF 帧先做 head-vs-head 复现（两构建同帧
同差）再定豁免，否则按真回归排查。目录模式完全一致（计豁免）退出码 0，有 DIFF 或缺帧退出 1，
无公共 seq 退出 2。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

FRAME_RE = re.compile(r"_frame(\d+)\.bin$")


def log(msg: str) -> None:
    print(f"[diff] {msg}", flush=True)


def analyze(a: bytes, b: bytes) -> dict:
    """逐像素 4 通道对比，返回统计与最差像素样本。"""
    stats = {"max_diff": 0, "nonzero_pixels": 0, "nonzero_channels": 0,
             "hist": {}, "worst": []}
    if len(a) != len(b):
        stats["size_mismatch"] = (len(a), len(b))
        return stats
    hist = {1: 0, 2: 0, 4: 0, 8: 0, 16: 0, 32: 0, 64: 0, 128: 0, 255: 0}
    for i in range(0, len(a), 4):
        pa = a[i:i + 4]
        pb = b[i:i + 4]
        d = [abs(pa[c] - pb[c]) for c in range(4)]
        pixel_max = max(d)
        if pixel_max > 0:
            stats["nonzero_pixels"] += 1
            stats["nonzero_channels"] += sum(1 for v in d if v > 0)
            if pixel_max > stats["max_diff"]:
                stats["max_diff"] = pixel_max
                stats["worst"] = [(i // 4, pa.hex(), pb.hex())]
            elif pixel_max == stats["max_diff"] and len(stats["worst"]) < 5:
                stats["worst"].append((i // 4, pa.hex(), pb.hex()))
            for bucket in sorted(hist):
                if pixel_max <= bucket:
                    hist[bucket] += 1
                    break
    stats["hist"] = hist
    return stats


def print_stats(stats: dict, prefix: str) -> None:
    if "size_mismatch" in stats:
        print(f"{prefix} DIFF size mismatch: {stats['size_mismatch'][0]} vs {stats['size_mismatch'][1]}")
        return
    pixels = "?"
    print(f"{prefix} max_channel_diff={stats['max_diff']} "
          f"nonzero_pixels={stats['nonzero_pixels']} "
          f"nonzero_channels={stats['nonzero_channels']}")
    if stats["nonzero_pixels"]:
        hist = stats["hist"]
        print(f"{prefix} diff<=1: {hist[1]} <=2: {hist[2]} <=4: {hist[4]} <=8: {hist[8]} "
              f"<=16: {hist[16]} <=32: {hist[32]} <=64: {hist[64]} <=128: {hist[128]}")
        print(f"{prefix} worst pixels (pixelIdx, a_rgba, b_rgba): {stats['worst']}")


def diff_file(path_a: str, path_b: str) -> int:
    a = open(path_a, "rb").read()
    b = open(path_b, "rb").read()
    if a == b:
        print("IDENTICAL")
        return 0
    stats = analyze(a, b)
    stats.setdefault("pixels", len(a) // 4)
    print(f"pixels={stats['pixels']} ({stats['pixels'] // 1000}K)")
    print_stats(stats, "")
    identical = stats.get("nonzero_pixels", 1) == 0 and "size_mismatch" not in stats
    print("IDENTICAL" if identical else "DIFFERENT")
    return 0 if identical else 1


def diff_dirs(dir_a: str, dir_b: str, skip: set[int]) -> int:
    def scan(path: str) -> dict[int, str]:
        found = {}
        for name in os.listdir(path):
            m = FRAME_RE.search(name)
            if m:
                found[int(m.group(1))] = os.path.join(path, name)
        return found

    frames_a = scan(dir_a)
    frames_b = scan(dir_b)
    common = sorted(set(frames_a) & set(frames_b))
    if not common:
        print(f"[diff] error: 无公共 seq (a={sorted(frames_a)[:10]}... b={sorted(frames_b)[:10]}...)")
        return 2
    identical, diff_seqs, skipped = 0, [], []
    missing_a = sorted(set(frames_b) - set(frames_a))
    missing_b = sorted(set(frames_a) - set(frames_b))
    for seq in common:
        if seq in skip:
            skipped.append(seq)
            print(f"seq {seq}: SKIPPED (豁免标注, 判读需 head-vs-head 证据)")
            continue
        a = open(frames_a[seq], "rb").read()
        b = open(frames_b[seq], "rb").read()
        if a == b:
            identical += 1
            print(f"seq {seq}: IDENTICAL")
            continue
        stats = analyze(a, b)
        if "size_mismatch" in stats or stats["nonzero_pixels"] > 0:
            diff_seqs.append(seq)
            print(f"seq {seq}: DIFFERENT")
            print_stats(stats, f"  seq {seq}:")
        else:
            identical += 1
            print(f"seq {seq}: IDENTICAL")
    compared = len(common) - len(skipped)
    print(f"SUMMARY: {identical}/{compared} IDENTICAL "
          f"(compared={compared}, diff={diff_seqs}, skipped={skipped}, "
          f"only_in_a={missing_b[:10]}, only_in_b={missing_a[:10]})")
    if diff_seqs or missing_a or missing_b or compared == 0:
        print("FAIL")
        return 1
    print("PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--skip-seq", default="", help="逗号分隔的 seq 列表，标记为豁免（不计入判定）")
    args = parser.parse_args()

    if os.path.isfile(args.a) and os.path.isfile(args.b):
        return diff_file(args.a, args.b)
    if os.path.isdir(args.a) and os.path.isdir(args.b):
        skip = {int(x) for x in args.skip_seq.split(",") if x.strip()}
        return diff_dirs(args.a, args.b, skip)
    print(f"[diff] error: 两参数须同为文件或同为目录: {args.a}, {args.b}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
