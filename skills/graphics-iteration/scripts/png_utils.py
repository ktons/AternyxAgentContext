#!/usr/bin/env python3
"""纯标准库 PNG 解码/编码、ROI 缩放与分位统计。

图形迭代脚本的共享底层：只处理 8bit、非隔行 PNG（颜色类型 0/2/4/6），
返回逐行原字节，供指标、缩略图与对拍脚本复用。依赖标准库 zlib/struct。
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

# ROI 参考分辨率：ROI 以该分辨率登记，实际帧按比例缩放。
REF_W, REF_H = 1920, 1080

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_RAW_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def decode_png_bytes(data: bytes) -> tuple[int, int, int, list[bytearray]]:
    """解码 PNG -> (width, height, channels, rows)；rows[y] 为逐像素原字节。"""
    if data[:8] != _PNG_MAGIC:
        raise ValueError("不是 PNG（magic 不符）")
    pos = 8
    width = height = depth = color_type = None
    interlace = 0
    idat = bytearray()
    while pos + 8 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, depth, color_type, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", payload)
        elif ctype == b"IDAT":
            idat += payload
        elif ctype == b"IEND":
            break
    if width is None or color_type is None:
        raise ValueError("PNG 缺 IHDR")
    if depth != 8:
        raise ValueError(f"PNG bit depth {depth} 不支持（只接受 8）")
    if interlace != 0:
        raise ValueError("PNG 隔行不支持")
    if color_type not in _PNG_RAW_CHANNELS:
        raise ValueError(f"PNG color type {color_type} 不支持")
    channels = _PNG_RAW_CHANNELS[color_type]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    if len(raw) < height * (stride + 1):
        raise ValueError(f"PNG 数据不足: {len(raw)} < {height * (stride + 1)}")
    rows: list[bytearray] = []
    prev = bytearray(stride)
    offset = 0
    for _y in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:  # Up
            line = bytearray([(a + b) & 0xFF for a, b in zip(line, prev)])
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = prev[i]
                up_left = prev[i - channels] if i >= channels else 0
                p = left + up - up_left
                pa = abs(p - left)
                pb = abs(p - up)
                pc = abs(p - up_left)
                if pa <= pb and pa <= pc:
                    pred = left
                elif pb <= pc:
                    pred = up
                else:
                    pred = up_left
                line[i] = (line[i] + pred) & 0xFF
        else:
            raise ValueError(f"PNG filter {filter_type} 不支持")
        rows.append(line)
        prev = line
    return width, height, channels, rows


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def write_png_rgb(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    """写 RGB8 PNG（filter 0），用于缩略图与 contact sheet。"""
    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw += row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (_PNG_MAGIC + _png_chunk(b"IHDR", ihdr)
               + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
               + _png_chunk(b"IEND", b""))
    path.write_bytes(payload)


def scale_roi(roi: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    """参考分辨率 ROI -> 实际分辨率（等比缩放、round 到整像素、夹在画面内）。"""
    x0, y0, x1, y1 = roi
    sx0 = min(width - 1, max(0, round(x0 * width / REF_W)))
    sy0 = min(height - 1, max(0, round(y0 * height / REF_H)))
    sx1 = min(width, max(sx0 + 1, round(x1 * width / REF_W)))
    sy1 = min(height, max(sy0 + 1, round(y1 * height / REF_H)))
    return sx0, sy0, sx1, sy1


def percentile(sorted_values: list[float], q: float) -> float | None:
    """线性插值分位（与 numpy.percentile 缺省口径一致），q 取 0..100。"""
    count = len(sorted_values)
    if count == 0:
        return None
    pos = (count - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, count - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac
