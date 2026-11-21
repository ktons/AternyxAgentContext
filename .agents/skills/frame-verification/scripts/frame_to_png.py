#!/usr/bin/env python3
"""4 字节/像素裸帧转 PNG（纯 zlib，无 PIL 依赖）。

输入 = 采集器产物 `<prefix>_frame<seq>.bin`（裸 RGBA8/BGRA8 行主序像素）。
尺寸自动从同目录 `<prefix>_meta.json` 读取；散 bin 用 --width/--height 显式给。
通道序按字节直写：BGRA 源转出的 PNG R/B 互换，目检时注意；量化脚本按字节语义
消费不受影响（黑/亮判定通道序无关）。

用法:
  python3 frame_to_png.py <bin> [<bin> ...] (--out <png 路径|目录>) \
      [--crop x,y,w,h] [--zoom N] [--swap-rb] [--width W] [--height H]
  --crop x,y,w,h: 先裁剪（缩放裁剪对比图 = 两组同 crop/zoom 参数各跑一次再排）;
  --zoom N: 最近邻放大 N 倍（特征区放大目检）;
  --swap-rb: 交换 R/B 通道（BGRA 源目检时用；不改变定量脚本的字节语义）。

多 bin 且 --out 为目录时输出名 = <bin stem>.png；--out 为 .png 路径时仅限单 bin。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import struct
import sys
import zlib


def log(msg: str) -> None:
    print(f"[frame_to_png] {msg}", flush=True)


def write_png(path: pathlib.Path, px: bytes, width: int, height: int) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    rows = b"".join(b"\x00" + px[y * width * 4:(y + 1) * width * 4] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def read_meta(bin_path: pathlib.Path) -> dict:
    meta_path = bin_path.parent / (bin_path.stem.rsplit("_frame", 1)[0] + "_meta.json")
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"WARN: {meta_path}: 读取失败 ({exc})")
        return {}


def resolve_size(bin_path: pathlib.Path, args) -> tuple[int, int]:
    if args.width and args.height:
        return args.width, args.height
    meta = read_meta(bin_path)
    if meta.get("width") and meta.get("height"):
        return int(meta["width"]), int(meta["height"])
    log(f"ERROR: {bin_path}: 无同名 meta.json 且未给 --width/--height")
    sys.exit(2)


def swap_rb(px: bytes) -> bytes:
    out = bytearray(px)
    out[0::4], out[2::4] = out[2::4], out[0::4]
    return bytes(out)


def convert(bin_path: pathlib.Path, out_path: pathlib.Path, args) -> None:
    width, height = resolve_size(bin_path, args)
    px = bin_path.read_bytes()
    if len(px) < width * height * 4:
        log(f"ERROR: {bin_path}: {len(px)}B < {width}x{height}x4")
        sys.exit(2)
    px = px[:width * height * 4]

    if args.crop:
        cx, cy, cw, ch = args.crop
        if cx + cw > width or cy + ch > height:
            log(f"ERROR: crop {args.crop} 越界 ({width}x{height})")
            sys.exit(2)
        raw = bytearray(cw * ch * 4)
        for y in range(ch):
            src = ((cy + y) * width + cx) * 4
            raw[y * cw * 4:(y + 1) * cw * 4] = px[src:src + cw * 4]
        px, width, height = bytes(raw), cw, ch

    if args.zoom > 1:
        zw, zh = width * args.zoom, height * args.zoom
        raw = bytearray(zw * zh * 4)
        for y in range(height):
            row = px[y * width * 4:(y + 1) * width * 4]
            grown = bytearray()
            for i in range(0, len(row), 4):
                grown += row[i:i + 4] * args.zoom
            for z in range(args.zoom):
                base = (y * args.zoom + z) * zw * 4
                raw[base:base + zw * 4] = grown
        px, width, height = bytes(raw), zw, zh

    if args.swap_rb:
        px = swap_rb(px)
    else:
        fmt = str(read_meta(bin_path).get("format") or "")
        if fmt.upper().startswith("BGRA"):
            log(f"NOTE: {bin_path}: format={fmt}，PNG 按字节直写（R/B 互换）；目检可加 --swap-rb")

    write_png(out_path, px, width, height)
    log(f"{out_path}: {width}x{height}" + (f" (crop={args.crop})" if args.crop else "")
        + (f" zoom={args.zoom}" if args.zoom > 1 else "")
        + (" swap-rb" if args.swap_rb else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bins", nargs="+", help="读回帧 .bin（frame_capture.py 产物）")
    parser.add_argument("--out", required=True, help="PNG 输出路径（单 bin）或目录（多 bin）")
    parser.add_argument("--crop", type=lambda s: tuple(int(v) for v in s.split(",")),
                        metavar="X,Y,W,H", help="先裁剪再输出")
    parser.add_argument("--zoom", type=int, default=1, help="最近邻放大倍数（缺省 1）")
    parser.add_argument("--swap-rb", action="store_true",
                        help="交换 R/B（BGRA 源目检用；定量脚本仍按字节语义）")
    parser.add_argument("--width", type=int, help="显式帧宽（无 meta 时必给）")
    parser.add_argument("--height", type=int, help="显式帧高（无 meta 时必给）")
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    if len(args.bins) > 1 and out.suffix == ".png":
        parser.error("多 bin 时 --out 须为目录")
    if out.suffix == ".png":
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out.mkdir(parents=True, exist_ok=True)

    for bin_path in (pathlib.Path(b) for b in args.bins):
        if not bin_path.exists():
            log(f"ERROR: {bin_path} 不存在")
            return 2
        out_path = out if out.suffix == ".png" else out / (bin_path.stem + ".png")
        convert(bin_path, out_path, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
