"""4 字节/像素裸帧逐字节对比：单文件，或按 seq 配对的目录。

两种模式:
    python3 frame_diff.py <a.bin> <b.bin>              # 单文件：同尺寸同格式, 4字节/像素
    python3 frame_diff.py <dir_a> <dir_b> [--skip-seq 0,7]   # 目录：按 seq 配对 <prefix>_frame<seq>.bin

单文件输出: 最大通道差 / 非零差像素数 / 差值分布；完全一致退出码 0, 否则 1。
目录模式先核对同名 *_meta.json 的 width/height/format/frames（缺 meta 时跳过并注明），
再对公共 seq 逐帧判定并给汇总行（N/M IDENTICAL）。--skip-seq 把指定 seq 标为 SKIPPED：不参与字节判定，
但本轮结论记为 SKIP（退出码 3），不能当作通过证据；豁免仍需同构建同帧复现后由调用方签字。
目录模式退出码: 0 PASS；1 FAIL（DIFF/缺帧/meta 不一致）；2 无公共 seq；3 SKIP（有跳过帧，其余一致）。
"""

from __future__ import annotations

import argparse
import glob
import json
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


def scan(path: str) -> dict[int, str]:
    found = {}
    for name in os.listdir(path):
        m = FRAME_RE.search(name)
        if m:
            found[int(m.group(1))] = os.path.join(path, name)
    return found


def load_meta(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        log(f"meta 读取失败 {path}: {exc}")
        return {}


def check_meta(dir_a: str, dir_b: str,
               frames_a: dict[int, str], frames_b: dict[int, str]) -> bool:
    """核对两侧 meta 的尺寸/格式/帧集合；无 meta 时跳过并注明。返回是否可比。"""
    metas_a = sorted(glob.glob(os.path.join(dir_a, "*_meta.json")))
    metas_b = sorted(glob.glob(os.path.join(dir_b, "*_meta.json")))
    if not metas_a and not metas_b:
        log("meta: 两侧均无 *_meta.json，跳过尺寸/格式核对")
        return True
    if not metas_a or not metas_b:
        log(f"meta: 仅一侧有 meta（a={len(metas_a)}, b={len(metas_b)}），跳过核对")
        return True

    by_name_b = {os.path.basename(p): p for p in metas_b}
    pairs = []
    for pa in metas_a:
        pb = by_name_b.get(os.path.basename(pa))
        if pb is None and len(metas_b) == 1:
            pb = metas_b[0]
        if pb is not None:
            pairs.append((pa, pb))
    if not pairs:
        log("meta: 两侧文件名不匹配，跳过核对")
        return True

    ok = True
    for pa, pb in pairs:
        ma, mb = load_meta(pa), load_meta(pb)
        if not ma or not mb:
            ok = False
            continue
        for key in ("width", "height", "format"):
            va, vb = ma.get(key), mb.get(key)
            if va is not None and vb is not None and va != vb:
                log(f"meta: {key} 不一致 a={va} b={vb}（不可比）")
                ok = False
        declared_a = set(ma.get("frames") or [])
        declared_b = set(mb.get("frames") or [])
        if declared_a and declared_b and declared_a != declared_b:
            log(f"meta: frames 集合不一致 "
                f"only_a={sorted(declared_a - declared_b)[:10]} "
                f"only_b={sorted(declared_b - declared_a)[:10]}")
            ok = False
        for side, declared, actual in (("a", declared_a, frames_a), ("b", declared_b, frames_b)):
            lost = sorted(declared - set(actual))
            if lost:
                log(f"meta: {side} 侧声明但缺文件 seq={lost[:10]}"
                    + ("..." if len(lost) > 10 else ""))
                ok = False
    if ok:
        log("meta: 尺寸/格式/帧集合一致")
    return ok


def diff_dirs(dir_a: str, dir_b: str, skip: set[int]) -> int:
    frames_a = scan(dir_a)
    frames_b = scan(dir_b)
    common = sorted(set(frames_a) & set(frames_b))
    if not common:
        print(f"[diff] error: 无公共 seq (a={sorted(frames_a)[:10]}... b={sorted(frames_b)[:10]}...)")
        return 2

    meta_ok = check_meta(dir_a, dir_b, frames_a, frames_b)
    identical, diff_seqs, skipped = 0, [], []
    missing_a = sorted(set(frames_b) - set(frames_a))
    missing_b = sorted(set(frames_a) - set(frames_b))
    for seq in common:
        if seq in skip:
            skipped.append(seq)
            print(f"seq {seq}: SKIPPED (不参与判定; 结论记为 SKIP, 需同构建同帧复现后由调用方签字)")
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
    if not meta_ok or diff_seqs or missing_a or missing_b:
        print("FAIL")
        return 1
    if skipped or compared == 0:
        print("SKIP: 存在被跳过的 seq，本轮不作为通过证据")
        return 3
    print("PASS")
    return 0


def parse_skip_seq(value: str) -> set[int]:
    out: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            raise argparse.ArgumentTypeError(f"非法 seq: {part!r}（应为逗号分隔的整数）") from None
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="4 字节/像素裸帧逐字节对比")
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--skip-seq", type=parse_skip_seq, default=set(), metavar="N,N",
                        help="逗号分隔的 seq：标为 SKIPPED，不参与判定；本轮结论记为 SKIP（退出码 3）")
    args = parser.parse_args()

    if os.path.isfile(args.a) and os.path.isfile(args.b):
        if args.skip_seq:
            parser.error("--skip-seq 只用于目录模式")
        return diff_file(args.a, args.b)
    if os.path.isdir(args.a) and os.path.isdir(args.b):
        return diff_dirs(args.a, args.b, args.skip_seq)
    print(f"[diff] error: 两参数须同为文件或同为目录: {args.a}, {args.b}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
