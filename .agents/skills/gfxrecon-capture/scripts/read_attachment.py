#!/usr/bin/env python3
"""读取 gfxrecon dump-resources 产出的附件 BMP 原始字节。

gfxrecon 的 BMP 为 bottom-up + BGR(A) 文件序; sips/预览对 32bpp 浮点格式
附件的转换会产生假色 (实测把暖白转成青色), 排查必须读本脚本这类原始字节读取。

用法:
  python3 read_attachment.py <attachment.bmp> --format R16G16B16A16_SFLOAT \
      --point 1280,1300 --point 400,1100
  python3 read_attachment.py <attachment.bmp> --point 1280,1300          # 不带 format 走旧输出
  python3 read_attachment.py <attachment.bmp> --scan-y 830 --x0 860 --x1 1150 [--step 20]
  python3 read_attachment.py <attachment.bmp> --histogram [--cell 4]

输出:
  - 每点: 文件字节序 (b0,b1,b2[,b3]) 与按 --format 通道标注的归一化值
    (如 R=0.251 G=0.239 B=0.224 A=1.000)。文件字节序恒为 B,G,R[,A]
    (深度/单通道图为灰度复制); --format 只决定通道语义与个数, 不改变字节序——
    RGBA 系格式: 文件[2],[1],[0],[3] 对应 R,G,B,A; BGRA 系: 文件序即通道序。
  - 浮点格式附件 (R16G16B16A16_SFLOAT 等) 被 gfxrecon 量化为 8bit BMP, 值只有 1/255 精度;
    用已知参照 (ambient 值/天空渐变) 标定后再读其他区域。
  - --histogram: 亮度/颜色分布, 用于找异常均匀块 (采样失效特征)。
"""
import argparse
import struct
import sys
from collections import Counter

# 常用 VkFormat → 通道语义 (gfxrecon 附件 BMP 的常见落地面); 未列出的格式按通道数退化为 RGBA 系
FORMAT_CHANNELS = {
    "D32_SFLOAT": ["D"], "D16_UNORM": ["D"], "D24_UNORM_S8_UINT": ["D"],
    "R8_UNORM": ["R"], "R16_UNORM": ["R"], "R16_SFLOAT": ["R"], "R32_SFLOAT": ["R"],
    "R8G8_UNORM": ["R", "G"], "R16G16_SFLOAT": ["R", "G"],
    "R8G8B8_UNORM": ["R", "G", "B"], "R16G16B16_SFLOAT": ["R", "G", "B"],
    "R8G8B8A8_UNORM": ["R", "G", "B", "A"], "R16G16B16A16_SFLOAT": ["R", "G", "B", "A"],
    "R32G32B32A32_SFLOAT": ["R", "G", "B", "A"],
    "B8G8R8A8_UNORM": ["B", "G", "R", "A"],
}


def load(path):
    d = open(path, "rb").read()
    off = struct.unpack("<I", d[10:14])[0]
    w = struct.unpack("<i", d[18:22])[0]
    h = struct.unpack("<i", d[22:26])[0]
    bpp = struct.unpack("<H", d[28:30])[0] // 8
    row_size = (w * bpp + 3) // 4 * 4

    def px(x, y):
        # BMP bottom-up: 文件第 0 行 = 图像最后一行; x 列内为 B,G,R[,A] 文件序
        base = off + (h - 1 - y) * row_size + x * bpp
        return tuple(d[base + i] for i in range(bpp))

    return w, abs(h), px


def resolve_format(fmt):
    """VK_FORMAT_ 前缀可省, 大小写不敏感; 未识别格式返回 None (调用方退化为旧行为)。"""
    if fmt is None:
        return None
    key = fmt.upper()
    if not key.startswith("VK_FORMAT_"):
        key = "VK_FORMAT_" + key
    return FORMAT_CHANNELS.get(key[len("VK_FORMAT_"):])


def map_channels(raw, channels):
    """文件字节 (B,G,R[,A] 序) → 按 channels 语义标注的归一化值。

    RGBA 系: 文件[2],[1],[0],[3] = R,G,B,A; BGRA 系/深度/单通道: 文件序直读。
    通道数少于字节数时取前 n 个语义通道; 深度/单通道走灰度复制 (byte[0])。
    """
    if channels is None:
        return None
    if channels[0] == "D":
        return {"D": raw[0] / 255}
    if channels == ["B", "G", "R", "A"]:
        return {ch: raw[i] / 255 for i, ch in enumerate(channels)}
    file_idx = {"R": 2, "G": 1, "B": 0, "A": 3}  # 文件 B,G,R,A 序中各语义通道的位置
    out = {}
    for ch in channels:
        out[ch] = raw[file_idx[ch]] / 255 if ch in file_idx else raw[0] / 255
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bmp")
    ap.add_argument("--format", help="附件 VkFormat (VK_FORMAT_ 前缀可省), 通道按格式语义标注")
    ap.add_argument("--point", action="append", default=[],
                    help="x,y 采样点, 可重复")
    ap.add_argument("--scan-y", type=int, help="沿某行扫描")
    ap.add_argument("--x0", type=int, default=0)
    ap.add_argument("--x1", type=int, default=None)
    ap.add_argument("--step", type=int, default=20)
    ap.add_argument("--histogram", action="store_true")
    ap.add_argument("--cell", type=int, default=4, help="直方图采样步长")
    args = ap.parse_args()

    channels = resolve_format(args.format)
    if args.format is not None and channels is None:
        print(f"未识别格式 {args.format} (可选: {', '.join(sorted(FORMAT_CHANNELS))}), 按原始字节输出")
        channels = None

    w, h, px = load(args.bmp)
    print(f"{w}x{h}" + (f"  通道: {''.join(channels)}" if channels else ""))

    if args.histogram:
        cnt = Counter()
        for y in range(0, h, args.cell):
            for x in range(0, w, args.cell):
                cnt[px(x, y)] += 1
        for color, n in cnt.most_common(8):
            label = ""
            if channels:
                mapped = map_channels(color, channels)
                label = "  " + " ".join(f"{k}={v:.3f}" for k, v in mapped.items())
            print(f"  {color} x{n}{label}")
        print(f"  独立颜色数: {len(cnt)}  (极少独立色+全图均匀 => 采样失效特征)")
        return

    if args.scan_y is not None:
        x1 = args.x1 if args.x1 is not None else w
        for x in range(args.x0, x1, args.step):
            raw = px(x, args.scan_y)
            label = ""
            if channels:
                mapped = map_channels(raw, channels)
                label = "  " + " ".join(f"{k}={v:.3f}" for k, v in mapped.items())
            print(f"  x={x}: {raw}{label}")
        return

    for p in args.point:
        x, y = (int(v) for v in p.split(","))
        raw = px(x, y)
        if channels is not None:
            mapped = map_channels(raw, channels)
            vals = " ".join(f"{k}={v:.3f}" for k, v in mapped.items())
            print(f"({x},{y}) 文件字节={raw}  {channels[0] if channels[0] == 'D' else '格式通道'}: {vals}")
        elif len(raw) >= 3:
            hlsl = (raw[2], raw[1], raw[0]) + raw[3:]  # 文件 BGR(A) -> HLSL RGBA
            print(f"({x},{y}) 文件字节={raw}  HLSL RGBA≈{hlsl}  (建议补 --format 消除通道序歧义)")
        else:
            print(f"({x},{y}) 灰度={raw}")


if __name__ == "__main__":
    sys.exit(main())
