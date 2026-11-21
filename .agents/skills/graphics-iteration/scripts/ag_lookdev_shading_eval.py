#!/usr/bin/env python3
"""ag_lookdev 中性判读面：39 预设 + 4 向 close-up 出图 / ROI 指标 / JSON / contact sheet。

用途（图形迭代 skill 母本，操作手册见 .agents/skills/graphics-iteration/references/02-capture.md
「ag_lookdev 出图判读」节）：把「ag_lookdev 场景的
可量化判读面」一次跑完并落盘，供每轮 lookdev 迭代复用同一口径的前后对比：
  1. 起离屏编辑器（--offscreen --no-cef-ui），scene.load ag_lookdev.scene；
  2. --post neutral 时经 component.set 整组件覆写 Volume（bloom/描边关断，其余字段按
     components() 读回原值回写；不虚构 lutTexture 引用），结束恢复并读回校验。
     bloom 关断写 bloomStrength=0（强度归零即真关断），并同时归零 bloomThreshold 作为缺省
     口径（对历史 bloom 打包序互换不敏感）。--neutral-bloom field-only 只写 bloomStrength=0，
     作字面对照；
  3. editor.captureAll 出 39 leg PNG（Game 视图，1920x1080 缺省）；
  4. 4 向 close-up：preset.apply face_front/left45/right45/back（同一机位窗，仅光向变化）后把
     MainCamera 沿自身前向推到距头部中心 --close-distance 米处再 capture，命名
     close_front/left45/right45/back；四张与对应预设帧、彼此之间做哈希比对（防冻帧）；
  5. 逐 leg 指标（纯标准库 PNG 解码 + 统计，PIL/numpy 不参与）：
       - 躯干 ROI（x 640..1280, y 290..580 @1920x1080 等比缩放）：
         天空掩码 b-r>0.06*255 && b>0.70*255 && r>0.47*255 + 排除 luma<=0.05（同
         temp/ag_review/tone.py 口径），p15/p50/p98 luma、暗部 RGB 均值与 R>G>B 判定、
         clip 比例（任一通道 >=250）；
       - face detail leg：face ROI（x 820..330..，见 FACE_ROI_REF）+ face_lr
         （face_left45 vs face_right45 同 ROI 逐像素通道最大差 |diff| 的 mean / >8 占比 /
         两图 ROI 均值 RGB，口径同 temp/ag_review/facediff3.py：不排除天空）+ face 组
         shadow_coverage（face ROI 内 luma<0.7 占比，排除天空与 luma<=0.05）；
       - close-up leg：全帧非天空掩码 p15/p50/p98 + 中心 40% 区域 luma<0.7 占比；
  6. 产物全部落 --out：capture_<preset>_<seq>.png / capture_close_<向>_<seq>.png（编辑器原始
     PNG，不改写内容）、eval_metrics.json、contact_sheet.png（4 列缩略图 240x135，无文字标签，
     顺序见 JSON contact_sheet_order）、editor.log（编辑器 stdout）、eval.log（本脚本日志）。

幂等/纪律：重跑前按 GatewaySession-<pid>.txt 清本工程残留编辑器（不用 shell kill）；采帧只做
运行时观察态改动（Volume / MainCamera Transform），结束恢复原值并读回校验；不写场景、不改
资产、不动 Git 历史（只 read-only 取 HEAD / git status 进 JSON）；不改 .scene/.mat/.shader。

用法（仓库根执行；Windows 用 python + 脚本自带 .exe 解析）：
  python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py   # 中性基线：39 + 4 close
  python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py --post scene --out temp/ag_t0_scene
  python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py --legs detail   # 只分析 detail 12 leg
  python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py --legs close    # 只出/只分析 4 向 close-up
  python .agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py --post neutral \
      --neutral-bloom field-only --out temp/ag_t0_neutral_fieldonly              # 字面口径对照（整幅 veil）
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import hashlib
import json
import logging
import math
import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

# 本脚本是图形迭代 skill 的母本；共享 Gateway 客户端仍归 Tools/scripts（其它工程工具 import 它）。
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "Tools" / "scripts"))
from gateway_client import GwClient, read_session_infos  # noqa: E402

LOG = logging.getLogger("ag_lookdev_shading_eval")
PROJECT_ROOT = REPO_ROOT / "Test" / "Projects" / "General"
SCENE = "Assets/scenes/ag_lookdev.scene"
EDITOR_EXE = REPO_ROOT / "build" / "bin" / (
    "AternyxEditorMain.exe" if os.name == "nt" else "AternyxEditorMain")
DEFAULT_OUT = "temp/ag_t0"

VOLUME_TYPE = "EnvironmentVolumeComponent"
TRANSFORM_TYPE = "TransformComponent"
CAMERA_ENTITY = "MainCamera"
VOLUME_ENTITY = "Volume"
# 中性 override（--post neutral）：bloom 关断 + 描边关断。
#   bloom 打包位 z=阈值 / w=强度（DeferredRenderPipeline 与 postprocess.hlsl 同序）曾与
#   消费位互换，已修；缺省把 bloomStrength 与 bloomThreshold 同时归零——强度归零即真关断，
#   双零对打包序不敏感（历史互换期只写 bloomStrength=0 会把阈值归零、整幅被强度 veil 洗白：
#   旧实测 (0.25,0.78)→(145,172,202) vs (0,0)→(124,152,185)）。
#   --neutral-bloom field-only 保留只写 bloomStrength=0 的对照口径。
NEUTRAL_BLOOM_DEFAULT = "off"
BLOOM_PACKING_NOTE = (
    "bloom 打包位 z=阈值 / w=强度（与 shader 消费位同序；历史曾互换，已修）："
    "强度归零即关断 bloom；缺省同时归零 bloomThreshold，对打包序不敏感，"
    "field-only 为只写 bloomStrength=0 的对照口径")


def neutral_fields(mode: str) -> dict:
    fields = {"bloomStrength": 0.0, "outlineWidth": 0.0}
    if mode == "off":
        fields["bloomThreshold"] = 0.0
    return fields

# ---- 判读口径（分辨率无关：按 --width/--height 等比缩放） ----
REF_W, REF_H = 1920, 1080
TORSO_ROI_REF = (640, 290, 1280, 580)   # 躯干+手臂带，地平线以上（tone.py 口径）
FACE_ROI_REF = (820, 330, 1100, 620)    # face 预设取景下的脸部框（facediff3.py 口径）
SKY_DR, SKY_B, SKY_R = 0.06, 0.70, 0.47  # sky = (b-r)/255 > 0.06 & b/255 > 0.70 & r/255 > 0.47
LUMA_R, LUMA_G, LUMA_B = 0.2126, 0.7152, 0.0722
LUMA_FLOOR = 0.05        # ROI 内排除近黑（旧 21px 黑描边像素）
SHADOW_LUMA = 0.7        # shadow_coverage / 中心 40% 的「影」阈值
CLIP_LEVEL = 250         # clip = 任一通道 >= 250

DEFAULT_CLOSE_DISTANCE = 0.85
DEFAULT_HEAD_CENTER = (0.0, 1.43, 0.0)  # 头部中心世界点（face 预设 look-at 点，实测校准）

BODY_CAMERAS = ("front", "left45", "right45", "back34", "profile90", "back180")
DETAIL_CAMERAS = ("face", "hair", "weapon")
MATRIX_ROWS = BODY_CAMERAS + DETAIL_CAMERAS
DIRECTIONS = ("front", "left45", "right45", "back")
CONDITIONS = ("warm_front", "cool_front", "lowkey_front")
CLOSE_LEGS = (
    ("face_front", "close_front"),
    ("face_left45", "close_left45"),
    ("face_right45", "close_right45"),
    ("face_back", "close_back"),
)

CONTACT_THUMB = (240, 135)
CONTACT_GAP = 4
CONTACT_BG = (24, 24, 28)


# ======================================================================================
# PNG 编解码（纯标准库：zlib + struct，bit depth 8 / 非隔行，filter 0-4）
# ======================================================================================

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_RAW_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def decode_png_bytes(data: bytes) -> tuple[int, int, int, list[bytearray]]:
    """解码 PNG -> (width, height, channels, rows)；rows[y] = 逐像素原字节（无 filter 字节）。

    只接受 bit depth 8、非隔行；颜色类型 0/2/4/6（编辑器 stb 出图为 6=RGBA）。
    """
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
    """RGB8 PNG 落盘（filter 0，逐行无滤波；contact sheet 专用）。"""
    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw += row
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = (_PNG_MAGIC + _png_chunk(b"IHDR", ihdr)
               + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
               + _png_chunk(b"IEND", b""))
    path.write_bytes(payload)


# ======================================================================================
# 指标
# ======================================================================================

def scale_roi(roi: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int]:
    """参考分辨率 ROI -> 实际分辨率（等比缩放，round 到整像素，夹在画面内）。"""
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


def _is_sky(r: int, g: int, b: int) -> bool:
    return (b - r) > SKY_DR * 255.0 and b > SKY_B * 255.0 and r > SKY_R * 255.0


def _luma(r: int, g: int, b: int) -> float:
    return (LUMA_R * r + LUMA_G * g + LUMA_B * b) / 255.0


def _dark_order(rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    return "R>G>B" if r > g > b else "not R>G>B"


def measure_roi(rows: list[bytearray], channels: int, roi: tuple[int, int, int, int]) -> dict:
    """ROI 内「非天空 + 非近黑」像素的 tone/color 指标（tone.py 口径）。

    返回 luma 分位、暗部（luma<=p15）RGB 均值与色序、clip 比例、面影覆盖（face 组用）。
    """
    x0, y0, x1, y1 = roi
    lums: list[float] = []
    clipped = 0
    shadow = 0
    for y in range(y0, y1):
        row = rows[y]
        for i in range(x0 * channels, x1 * channels, channels):
            r = row[i]
            g = row[i + 1]
            b = row[i + 2]
            if _is_sky(r, g, b):
                continue
            value = _luma(r, g, b)
            if value <= LUMA_FLOOR:
                continue
            lums.append(value)
            if r >= CLIP_LEVEL or g >= CLIP_LEVEL or b >= CLIP_LEVEL:
                clipped += 1
            if value < SHADOW_LUMA:
                shadow += 1
    lums.sort()
    count = len(lums)
    p15 = percentile(lums, 15.0)
    p50 = percentile(lums, 50.0)
    p98 = percentile(lums, 98.0)
    dark_rgb = [0.0, 0.0, 0.0]
    dark_count = 0
    if p15 is not None:
        for y in range(y0, y1):
            row = rows[y]
            for i in range(x0 * channels, x1 * channels, channels):
                r = row[i]
                g = row[i + 1]
                b = row[i + 2]
                if _is_sky(r, g, b):
                    continue
                if _luma(r, g, b) > p15:
                    continue
                dark_rgb[0] += r
                dark_rgb[1] += g
                dark_rgb[2] += b
                dark_count += 1
    if dark_count:
        dark_rgb = [value / dark_count for value in dark_rgb]
    else:
        dark_rgb = [0.0, 0.0, 0.0]
    return {
        "roi_pixels": (x1 - x0) * (y1 - y0),
        "pixel_count": count,
        "luma": {
            "p15": None if p15 is None else round(p15, 6),
            "p50": None if p50 is None else round(p50, 6),
            "p98": None if p98 is None else round(p98, 6),
            "p98_over_p15": None if not p15 else round(p98 / p15, 4),
        },
        "dark": {
            "definition": "luma<=p15 within analyzed mask",
            "pixel_count": dark_count,
            "frac": round(dark_count / count, 6) if count else None,
            "rgb": [round(value, 3) for value in dark_rgb],
            "order": _dark_order(tuple(dark_rgb)),
        },
        "clip_frac": round(clipped / count, 6) if count else None,
        "shadow_coverage": round(shadow / count, 6) if count else None,
    }


def measure_close(rows: list[bytearray], channels: int, width: int, height: int) -> dict:
    """close-up 全帧：非天空掩码 luma 分位 + 中心 40% 区域 luma<0.7 占比 + 主体框。"""
    lums: list[float] = []
    clipped = 0
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        row = rows[y]
        base = 0
        for x in range(width):
            i = base
            base += channels
            r = row[i]
            g = row[i + 1]
            b = row[i + 2]
            if _is_sky(r, g, b):
                continue
            value = _luma(r, g, b)
            lums.append(value)
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
            if r >= CLIP_LEVEL or g >= CLIP_LEVEL or b >= CLIP_LEVEL:
                clipped += 1
    lums.sort()
    count = len(lums)
    p15 = percentile(lums, 15.0)
    p50 = percentile(lums, 50.0)
    p98 = percentile(lums, 98.0)
    dark_rgb = [0.0, 0.0, 0.0]
    dark_count = 0
    if p15 is not None:
        for y in range(height):
            row = rows[y]
            for i in range(0, width * channels, channels):
                r = row[i]
                g = row[i + 1]
                b = row[i + 2]
                if _is_sky(r, g, b):
                    continue
                if _luma(r, g, b) > p15:
                    continue
                dark_rgb[0] += r
                dark_rgb[1] += g
                dark_rgb[2] += b
                dark_count += 1
    if dark_count:
        dark_rgb = [value / dark_count for value in dark_rgb]
    else:
        dark_rgb = [0.0, 0.0, 0.0]
    # 中心 40%（0.3..0.7 两轴）内 luma<0.7 的原始像素占比（不排除天空，中心区域应为纯主体）
    cx0, cx1 = int(width * 0.3), int(width * 0.7)
    cy0, cy1 = int(height * 0.3), int(height * 0.7)
    center_total = 0
    center_dark = 0
    for y in range(cy0, cy1):
        row = rows[y]
        for i in range(cx0 * channels, cx1 * channels, channels):
            value = _luma(row[i], row[i + 1], row[i + 2])
            center_total += 1
            if value < SHADOW_LUMA:
                center_dark += 1
    bbox = None
    if max_x >= 0:
        bbox = {"x0": min_x, "y0": min_y, "x1": max_x, "y1": max_y,
                "width": max_x - min_x + 1, "height": max_y - min_y + 1,
                "center": [(min_x + max_x) / 2.0, (min_y + max_y) / 2.0],
                "height_frac": round((max_y - min_y + 1) / height, 4)}
    return {
        "mask": "non-sky (luma<=0.05 kept: close-up has no outline band)",
        "pixel_count": count,
        "nonsky_frac": round(count / float(width * height), 6),
        "luma": {
            "p15": None if p15 is None else round(p15, 6),
            "p50": None if p50 is None else round(p50, 6),
            "p98": None if p98 is None else round(p98, 6),
            "p98_over_p15": None if not p15 else round(p98 / p15, 4),
        },
        "dark": {
            "definition": "luma<=p15 within non-sky mask",
            "pixel_count": dark_count,
            "frac": round(dark_count / count, 6) if count else None,
            "rgb": [round(value, 3) for value in dark_rgb],
            "order": _dark_order(tuple(dark_rgb)),
        },
        "clip_frac": round(clipped / count, 6) if count else None,
        "subject_bbox": bbox,
        "center40": {
            "rect": [cx0, cy0, cx1, cy1],
            "dark_frac": round(center_dark / center_total, 6) if center_total else None,
        },
    }


def face_lr_diff(left: list[bytes], right: list[bytes], channels: int,
                 roi: tuple[int, int, int, int]) -> dict:
    """face_left45 vs face_right45 同 ROI 逐像素对比（facediff3.py 口径：不排除天空）。

    left/right = _roi_rows 裁好的 ROI 行（已从 x0 起、长度为 ROI 宽），roi 只用于回报坐标。
    """
    x0, y0, x1, y1 = roi
    width = x1 - x0
    total = 0
    diff_sum = 0.0
    max_diff = 0
    over8 = 0
    left_sum = [0.0, 0.0, 0.0]
    right_sum = [0.0, 0.0, 0.0]
    for row_l, row_r in zip(left, right):
        for i in range(0, width * channels, channels):
            for c in range(3):
                left_sum[c] += row_l[i + c]
                right_sum[c] += row_r[i + c]
            d = 0
            for c in range(3):
                delta = row_l[i + c] - row_r[i + c]
                if delta < 0:
                    delta = -delta
                if delta > d:
                    d = delta
            total += 1
            diff_sum += d
            if d > max_diff:
                max_diff = d
            if d > 8:
                over8 += 1
    left_mean = [round(value / total, 3) for value in left_sum] if total else [0.0, 0.0, 0.0]
    right_mean = [round(value / total, 3) for value in right_sum] if total else [0.0, 0.0, 0.0]
    return {
        "roi": list(roi),
        "pixel_count": total,
        "mean_abs_diff": round(diff_sum / total, 4) if total else None,
        "max_abs_diff": max_diff,
        "frac_gt8": round(over8 / total, 6) if total else None,
        "left_mean_rgb": left_mean,
        "right_mean_rgb": right_mean,
        "channel_mean_diff": [round(left_mean[c] - right_mean[c], 4) for c in range(3)],
        "max_abs_channel_mean_diff": max(
            abs(left_mean[c] - right_mean[c]) for c in range(3)) if total else None,
    }


def _roi_rows(rows: list[bytearray], channels: int, roi: tuple[int, int, int, int]) -> list[bytes]:
    x0, y0, x1, y1 = roi
    return [bytes(rows[y][x0 * channels:x1 * channels]) for y in range(y0, y1)]


# ======================================================================================
# contact sheet（纯标准库：解码后的缩略图 4 列拼板 + 自写 RGB PNG）
# ======================================================================================

def make_thumbnail(rows: list[bytearray], channels: int, width: int, height: int,
                   thumb_w: int = CONTACT_THUMB[0], thumb_h: int = CONTACT_THUMB[1]) -> list[bytes]:
    """2x2 采样的块平均降采样 -> RGB 行（缩略图只作索引，不做逐像素保真）。"""
    xs = []
    for dx in range(thumb_w):
        x0 = dx * width // thumb_w
        x1 = max(x0, (dx + 1) * width // thumb_w - 1)
        xs.append((x0 * channels, x1 * channels))
    out: list[bytes] = []
    for dy in range(thumb_h):
        y0 = dy * height // thumb_h
        y1 = max(y0, (dy + 1) * height // thumb_h - 1)
        row0 = rows[y0]
        row1 = rows[y1]
        line = bytearray(thumb_w * 3)
        for dx, (i0, i1) in enumerate(xs):
            o = dx * 3
            line[o] = (row0[i0] + row0[i1] + row1[i0] + row1[i1] + 2) >> 2
            line[o + 1] = (row0[i0 + 1] + row0[i1 + 1] + row1[i0 + 1] + row1[i1 + 1] + 2) >> 2
            line[o + 2] = (row0[i0 + 2] + row0[i1 + 2] + row1[i0 + 2] + row1[i1 + 2] + 2) >> 2
        out.append(bytes(line))
    return out


def write_contact_sheet(path: Path, layout: list[list[str | None]],
                        thumbs: dict[str, list[bytes]]) -> tuple[int, int]:
    """4 列缩略图拼板（无文字标签；缺失格子留背景色）。"""
    thumb_w, thumb_h = CONTACT_THUMB
    columns = max(len(row) for row in layout)
    width = columns * thumb_w + (columns + 1) * CONTACT_GAP
    height = len(layout) * thumb_h + (len(layout) + 1) * CONTACT_GAP
    canvas = bytearray(CONTACT_BG * (width * height))
    for row_index, row in enumerate(layout):
        y = CONTACT_GAP + row_index * (thumb_h + CONTACT_GAP)
        for column, leg in enumerate(row):
            if leg is None or leg not in thumbs:
                continue
            x = CONTACT_GAP + column * (thumb_w + CONTACT_GAP)
            thumb = thumbs[leg]
            for ty in range(thumb_h):
                start = ((y + ty) * width + x) * 3
                canvas[start:start + thumb_w * 3] = thumb[ty]
    rows = [bytes(canvas[(y * width) * 3:(y + 1) * width * 3]) for y in range(height)]
    write_png_rgb(path, width, height, rows)
    return width, height


def contact_layout(available: set[str], legs: str, close_done: bool) -> list[list[str | None]]:
    """contact sheet 行序：9 条矩阵机位行（4 光向）+ 光照条件 1 行 + close 1 行。

    注：face/hair/weapon 三行本就属于 36 条矩阵（9 机位 x 4 光向）的行，不另起重复行；
    实际行/格顺序即 JSON contact_sheet_order。
    """
    layout: list[list[str | None]] = []
    if legs != "close":
        for camera in MATRIX_ROWS:
            row: list[str | None] = [
                f"{camera}_{direction}" if f"{camera}_{direction}" in available else None
                for direction in DIRECTIONS]
            if any(cell is not None for cell in row):
                layout.append(row)
        condition_row: list[str | None] = [
            name if name in available else None for name in CONDITIONS]
        if any(cell is not None for cell in condition_row):
            layout.append(condition_row + [None] * (4 - len(condition_row)))
    if close_done:
        layout.append([f"close_{name}" if f"close_{name}" in available else None
                       for name in DIRECTIONS])
    return layout


# ======================================================================================
# 数学（引擎约定：左手系 Y-up，相机前向 = 局部 +Z，四元数 xyzw）
# ======================================================================================

def quat_rotate(quat: list[float], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    """q * v * conj(q)（单位四元数），对齐 Quaternion::operator*(Vector3f)。"""
    x, y, z, w = (float(v) for v in quat)
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length <= 1e-12:
        return vector
    x, y, z, w = x / length, y / length, z / length, w / length
    vx, vy, vz = vector
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx))


# ======================================================================================
# 编辑器生命周期 / Git 只读信息
# ======================================================================================

def _terminate_pid(pid: int) -> None:
    """按 pid 结束进程：Windows 走 OpenProcess+TerminateProcess（不用 shell kill 命令）。"""
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def stop_project_editors() -> list[int]:
    """按会话文件清本工程残留编辑器（幂等重跑前置）。"""
    killed = []
    for info in read_session_infos():
        if info["projectPath"] == str(PROJECT_ROOT):
            LOG.warning("清理残留编辑器 pid=%s (%s)", info["pid"], info["path"])
            _terminate_pid(info["pid"])
            killed.append(info["pid"])
    if killed:
        time.sleep(1.0)
    return killed


def launch_editor(log_path: Path, timeout: float) -> tuple[subprocess.Popen, object, float]:
    """起编辑器并等会话文件就绪；返回 (process, log_file, startup_seconds)。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [str(EDITOR_EXE), "--offscreen", "--no-cef-ui", "-p", str(PROJECT_ROOT),
               # --maxfps 0: 编辑器离屏形态 CLI 有 30fps 缺省限帧, lookdev 采帧评估按不限帧
               # 口径校准 (真实时钟场景帧内容随墙钟相位), 显式 0 保持既有采集语义零变化
               "--engine-root", str(REPO_ROOT), "--maxfps", "0"]
    LOG.info("起编辑器: %s", " ".join(command))
    started = time.time()
    log_file = open(log_path, "wb")
    process = subprocess.Popen(command, cwd=str(REPO_ROOT / "build" / "bin"),
                               stdout=log_file, stderr=subprocess.STDOUT)
    try:
        while time.time() - started < timeout:
            if process.poll() is not None:
                log_file.close()
                tail = log_path.read_text(errors="replace")[-2000:]
                raise SystemExit(f"编辑器提前退出 ({process.returncode}):\n{tail}")
            infos = [i for i in read_session_infos() if i["projectPath"] == str(PROJECT_ROOT)]
            if infos:
                LOG.info("会话就绪 pid=%s port=%s (%.1fs)", infos[0]["pid"],
                         infos[0]["tcpPort"], time.time() - started)
                return process, log_file, time.time() - started
            time.sleep(0.4)
    except BaseException:
        _stop_editor(process)
        log_file.close()
        raise
    _stop_editor(process)
    log_file.close()
    raise SystemExit(f"等待编辑器会话文件超时 ({timeout:.0f}s)")


def _stop_editor(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            LOG.warning("编辑器 kill 后仍不退出 pid=%s", process.pid)


def connect_client() -> GwClient:
    client = GwClient.from_session_file(project_path=str(PROJECT_ROOT))
    last_error: Exception | None = None
    for _attempt in range(6):
        try:
            client.connect()
            client.upgrade_mutate()
            return client
        except (OSError, ConnectionError, TimeoutError) as error:  # 启动竞态：重试
            last_error = error
            time.sleep(0.5)
    raise SystemExit(f"Gateway 连接失败: {last_error}")


def git_read(args: list[str], default: str = "") -> str:
    try:
        result = subprocess.run(["git", "-C", str(REPO_ROOT)] + args, capture_output=True,
                                text=True, timeout=30)
        return result.stdout.strip() if result.returncode == 0 else default
    except (OSError, subprocess.SubprocessError):
        return default


def git_status_lines() -> list[str]:
    text = git_read(["status", "--short"])
    return [line for line in text.splitlines() if line.strip()] if text else []


def count_log_lines(log_path: Path) -> dict:
    """编辑器日志按级别计数（格式 [timestamp][level][thread]）；ERROR 必须为 0。"""
    errors: list[str] = []
    warnings = 0
    if log_path.is_file():
        for line in log_path.read_text(errors="replace").splitlines():
            if "[error]" in line:
                if len(errors) < 20:
                    errors.append(line[:300])
            elif "[warning]" in line:
                warnings += 1
    return {"error_count": len(errors), "warning_count": warnings, "error_samples": errors}


# ======================================================================================
# Gateway 编排
# ======================================================================================

def resolve_rig_children(client: GwClient) -> tuple[dict, dict, dict]:
    """rig 寻址：preset.list 回显 entity -> tree 子实体按名取 MainCamera / Volume。"""
    listing = client.preset_list()
    rig = listing["entity"]
    children: dict[str, dict] = {}
    for entity in client.tree()["entities"]:
        parent = entity.get("parent")
        if (parent and parent["index"] == rig["index"]
                and parent["generation"] == rig["generation"]):
            children[entity["name"]] = entity["id"]
    for name in (CAMERA_ENTITY, VOLUME_ENTITY):
        if name not in children:
            raise SystemExit(f"rig 下找不到实体 '{name}'（children={sorted(children)}）")
    return rig, children[CAMERA_ENTITY], children[VOLUME_ENTITY]


def read_transform(client: GwClient, entity: dict) -> dict:
    return client.components(entity["index"], entity["generation"])[TRANSFORM_TYPE]


def read_volume(client: GwClient, entity: dict) -> dict:
    return client.components(entity["index"], entity["generation"])[VOLUME_TYPE]


def wait_camera_moved(client: GwClient, camera: dict, prev_position: list[float] | None,
                      timeout: float = 3.0) -> tuple[bool, dict, float]:
    """等 preset.seek 的次帧写回落盘：相机位置相对上次观测发生变化（或超时）。"""
    started = time.time()
    transform = read_transform(client, camera)
    while time.time() - started < timeout:
        position = transform["position"]
        if prev_position is None or any(
                abs(position[i] - prev_position[i]) > 1e-4 for i in range(3)):
            return True, transform, time.time() - started
        time.sleep(0.1)
        transform = read_transform(client, camera)
    return False, transform, time.time() - started


def capture_all_legs(client: GwClient, out_dir: Path, width: int, height: int,
                     timeout: float) -> tuple[dict, list[dict]]:
    """editor.captureAll（宽度/高度显式下发以钉住分辨率；gwclient 的封装不带尺寸参数）。"""
    started = time.time()
    batch = client.request("editor.captureAll",
                           {"path": str(out_dir), "width": width, "height": height},
                           deferred=True, timeout=timeout)["result"]
    elapsed = time.time() - started
    items = batch.get("items", [])
    LOG.info("editor.captureAll: %s/%s 成功, %s 失败 (%.1fs)", batch.get("captured"),
             batch.get("total"), batch.get("failed"), elapsed)
    if batch.get("failed"):
        failures = [item for item in items if not item.get("ok")]
        raise SystemExit(f"captureAll 有失败 leg: {failures[:3]}")
    return {"total": batch.get("total"), "captured": batch.get("captured"),
            "failed": batch.get("failed"), "dir": batch.get("dir"),
            "elapsed_seconds": round(elapsed, 3)}, items


def capture_single(client: GwClient, out_dir: Path, width: int, height: int,
                   timeout: float = 300.0) -> dict:
    """不带 preset 的单点 capture（当前观察态出图）；refresh=false 避免逐张重导。"""
    return client.request("editor.capture",
                          {"path": str(out_dir), "refresh": False,
                           "width": width, "height": height},
                          deferred=True, timeout=timeout)["result"]


# ======================================================================================
# 主流程
# ======================================================================================

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ag_lookdev 中性判读面：39 预设 + 4 向 close-up 出图/ROI 指标/contact sheet",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"产物目录（缺省 {DEFAULT_OUT}，相对仓库根）")
    parser.add_argument("--post", choices=("neutral", "scene"), default="neutral",
                        help="neutral=临时关 bloom/outline（缺省）；scene=原样（当前 HEAD 画面）")
    parser.add_argument("--neutral-bloom", choices=("off", "field-only"), default=NEUTRAL_BLOOM_DEFAULT,
                        help="off=bloomStrength 与 bloomThreshold 同时归零（缺省，强度归零真关断且对打包序不敏感）；"
                             "field-only=只写 bloomStrength=0 的字面对照口径")
    parser.add_argument("--legs", choices=("matrix", "detail", "all", "close"), default="all",
                        help="matrix=36 矩阵 leg；detail=12 detail leg；all=39（缺省）；close=仅 4 向 close-up")
    parser.add_argument("--width", type=int, default=1920,
                        help="出图宽（必须等于 game view 实际渲染尺寸，离屏缺省 1920；"
                             "editor.capture 只校验不改渲染分辨率）")
    parser.add_argument("--height", type=int, default=1080, help="出图高（同 --width 约束）")
    parser.add_argument("--close-distance", type=float, default=DEFAULT_CLOSE_DISTANCE,
                        help=f"close-up 相机到头部中心的距离（缺省 {DEFAULT_CLOSE_DISTANCE}m）")
    parser.add_argument("--head-center", type=float, nargs=3, default=list(DEFAULT_HEAD_CENTER),
                        metavar=("X", "Y", "Z"),
                        help=f"头部中心世界点（缺省 {DEFAULT_HEAD_CENTER}）")
    parser.add_argument("--skip-close", action="store_true", help="跳过 4 向 close-up")
    parser.add_argument("--keep-editor", action="store_true", help="调试用：结束时不杀编辑器")
    parser.add_argument("--editor-timeout", type=float, default=120.0, help="等会话文件超时（秒）")
    parser.add_argument("--capture-timeout", type=float, default=900.0, help="captureAll 超时（秒）")
    return parser.parse_args(argv)


def preset_group(name: str) -> str:
    if name in CONDITIONS:
        return "condition"
    camera = name.split("_", 1)[0]
    if camera in DETAIL_CAMERAS:
        return camera
    if camera in BODY_CAMERAS:
        return "body"
    return "unknown"


def leg_roi_kind(group: str) -> str:
    return "face" if group == "face" else "torso"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("capture_*.png"):
        stale.unlink()

    logging.basicConfig(level=logging.INFO,
                        format="[%(levelname)s] %(name)s: %(message)s")
    file_handler = logging.FileHandler(out_dir / "eval.log", encoding="utf-8", mode="w")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    LOG.addHandler(file_handler)

    close_enabled = not args.skip_close and args.legs in ("all", "close")
    if args.legs == "close" and args.skip_close:
        raise SystemExit("--legs close 与 --skip-close 组合没有任何出图任务")
    run_batch = args.legs in ("matrix", "detail", "all")
    # 分析子集：matrix = 36 条矩阵（body + face/hair/weapon 机位），detail = 12 条 detail，
    # all = 39 条（含 3 条光照条件），close = 仅 close-up；None 表示不过滤
    wanted_groups = {
        "matrix": {"body", "face", "hair", "weapon"},
        "detail": {"face", "hair", "weapon"},
    }.get(args.legs)
    LOG.info("参数: out=%s post=%s legs=%s %dx%d close=%s(%.2fm @ %s)",
             out_dir, args.post, args.legs, args.width, args.height,
             close_enabled, args.close_distance, tuple(args.head_center))

    status_before = git_status_lines()
    started_at = time.time()
    report: dict = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "script": ".agents/skills/graphics-iteration/scripts/ag_lookdev_shading_eval.py",
        "commit": git_read(["rev-parse", "HEAD"]),
        "branch": git_read(["rev-parse", "--abbrev-ref", "HEAD"]),
        "git_status_before": status_before,
        "arguments": {
            "out": str(out_dir), "post": args.post, "legs": args.legs,
            "width": args.width, "height": args.height,
            "skip_close": args.skip_close, "keep_editor": args.keep_editor,
            "close_distance_m": args.close_distance,
            "head_center": list(args.head_center),
        },
        "scene": SCENE,
        "project_root": str(PROJECT_ROOT),
        "resolution": {"width": args.width, "height": args.height},
        "post": args.post,
        "metric_definitions": {
            "roi_reference_resolution": [REF_W, REF_H],
            "torso_roi": list(TORSO_ROI_REF),
            "face_roi": list(FACE_ROI_REF),
            "sky_mask": "(b-r)/255 > 0.06 and b/255 > 0.70 and r/255 > 0.47",
            "roi_luma_floor": LUMA_FLOOR,
            "shadow_luma": SHADOW_LUMA,
            "clip_level": CLIP_LEVEL,
            "luma": "0.2126*r + 0.7152*g + 0.0722*b (sRGB 值域 0..1)",
            "dark_rgb": "analyzed mask 内 luma<=p15 像素的 RGB 均值（色序 R>G>B 判定）",
            "face_lr": "face_left45/right45 同 face ROI 逐像素通道最大差；不排除天空（facediff3.py 口径）",
            "face_shadow_coverage": "face ROI 内 luma<0.7 占比（排除天空与 luma<=0.05）",
            "close": "全帧非天空掩码 luma 分位 + 中心 40% 区域 luma<0.7 占比",
        },
    }
    if args.legs in ("matrix", "detail"):
        report["analysis_filter"] = {
            "legs": args.legs,
            "note": "captureAll 固定出全部 39 条预设（编辑器侧无子集参数），本脚本只分析所选子集",
        }

    killed = stop_project_editors()
    if killed:
        report["stale_editors_terminated"] = killed

    editor_log = out_dir / "editor.log"
    process: subprocess.Popen | None = None
    log_file = None
    client: GwClient | None = None
    volume_before: dict = {}
    camera_before: dict = {}
    volume_restore_ok: bool | None = None
    camera_restore_ok: bool | None = None
    batch_info: dict = {}
    batch_items: list[dict] = []
    close_records: list[dict] = []
    thumbnails: dict[str, list[bytes]] = {}
    close_ok = False
    startup_seconds = 0.0
    try:
        process, log_file, startup_seconds = launch_editor(editor_log, args.editor_timeout)
        client = connect_client()
        client.scene_load(SCENE)
        time.sleep(1.5)  # clip 资产 LoadAsync 完成
        rig, camera, volume = resolve_rig_children(client)
        camera_entity = camera
        volume_entity = volume
        listing = client.preset_list()
        preset_names = [preset["name"] for preset in listing.get("presets", [])]
        report["rig"] = {"entity": rig}
        report["entities"] = {"camera": camera_entity, "volume": volume_entity}
        report["presets"] = {"count": len(preset_names), "names": preset_names}
        if len(preset_names) != 39:
            LOG.warning("预设数 %d != 39（场景已变更？ROI/contact sheet 口径按 39 条设计）",
                        len(preset_names))

        volume_before = read_volume(client, volume_entity)
        camera_before = read_transform(client, camera_entity)
        report["volume_before"] = volume_before
        report["camera_before"] = camera_before
        LOG.info("Volume 原值: bloomStrength=%s outlineWidth=%s exposure=%s ambient=%s",
                 volume_before.get("bloomStrength"), volume_before.get("outlineWidth"),
                 volume_before.get("exposure"), volume_before.get("ambientIntensity"))

        override_info: dict = {"applied": False}
        if args.post == "neutral":
            fields = neutral_fields(args.neutral_bloom)
            already_neutral = all(volume_before.get(key) == value
                                  for key, value in fields.items())
            override = dict(volume_before)
            override.update(fields)
            client.set_component(volume_entity["index"], volume_entity["generation"],
                                 VOLUME_TYPE, override)
            readback = read_volume(client, volume_entity)
            verified = all(readback.get(key) == value for key, value in fields.items())
            consistent = all(readback.get(key) == volume_before.get(key)
                             for key in volume_before if key not in fields)
            override_info = {
                "applied": True,
                "bloom_mode": args.neutral_bloom,
                "already_neutral": already_neutral,
                "fields": fields,
                "volume_after_override": readback,
                "verified": verified,
                "other_fields_unchanged": consistent,
                "bloom_packing_note": BLOOM_PACKING_NOTE,
            }
            LOG.info("中性 override(%s): verified=%s other_fields_unchanged=%s (already=%s)",
                     args.neutral_bloom, verified, consistent, already_neutral)
            if not verified or not consistent:
                raise SystemExit("中性 override 读回不符，终止（不产出口径漂移的指标）")
        report["neutral_override"] = override_info

        if run_batch:
            batch_info, batch_items = capture_all_legs(client, out_dir, args.width, args.height,
                                                       args.capture_timeout)
        report["capture_batch"] = batch_info or None

        if close_enabled:
            close_started = time.time()
            head = tuple(args.head_center)
            previous_position = list(camera_before["position"])
            for preset_name, leg_name in CLOSE_LEGS:
                client.preset_apply(preset_name)
                moved, transform, waited = wait_camera_moved(client, camera_entity,
                                                             previous_position)
                forward = quat_rotate(transform["rotation"], (0.0, 0.0, 1.0))
                position = [head[i] - forward[i] * args.close_distance for i in range(3)]
                payload = dict(transform)
                payload["position"] = position
                client.set_component(camera_entity["index"], camera_entity["generation"],
                                     TRANSFORM_TYPE, payload)
                readback = read_transform(client, camera_entity)
                applied = max(abs(readback["position"][i] - position[i])
                              for i in range(3)) <= 1e-4
                shot = capture_single(client, out_dir, args.width, args.height)
                source = Path(shot["path"])
                if not source.is_file():
                    raise SystemExit(f"capture 产物缺失: {source}")
                target = out_dir / f"capture_{leg_name}_{shot['seq']}.png"
                if target.exists():
                    raise SystemExit(f"close-up 目标文件已存在（重名）: {target}")
                source.replace(target)
                previous_position = list(position)
                close_records.append({
                    "leg": leg_name,
                    "preset": preset_name,
                    "seq": shot["seq"],
                    "file": target.name,
                    "camera_position": [round(value, 6) for value in position],
                    "camera_rotation": [round(float(value), 8)
                                        for value in transform["rotation"]],
                    "forward": [round(value, 6) for value in forward],
                    "head_center": [round(value, 6) for value in head],
                    "distance_m": args.close_distance,
                    "seek_camera_moved": moved,
                    "seek_wait_seconds": round(waited, 3),
                    "transform_applied": applied,
                })
                LOG.info("close-up %-11s preset=%-11s seq=%s pos=%s moved=%s applied=%s",
                         leg_name, preset_name, shot["seq"],
                         [round(value, 3) for value in position], moved, applied)
                if not applied:
                    raise SystemExit(f"close-up {leg_name} 相机 Transform 写回校验失败")
            report["close_elapsed_seconds"] = round(time.time() - close_started, 3)
            report["close_camera_positions"] = {
                "head_center": [round(value, 6) for value in head],
                "distance_m": args.close_distance,
                "method": "preset.apply(face_*) -> 读 MainCamera Transform -> 沿其前向 (+Z) "
                          "把相机放到 head_center - forward*distance -> component.set -> capture",
                "legs": {
                    record["leg"]: {
                        "preset": record["preset"],
                        "position": record["camera_position"],
                        "rotation": record["camera_rotation"],
                        "forward": record["forward"],
                        "seq": record["seq"],
                        "file": record["file"],
                        "seek_camera_moved": record["seek_camera_moved"],
                        "seek_wait_seconds": record["seek_wait_seconds"],
                    } for record in close_records
                },
            }
            close_ok = True

        # ---- 恢复观察态（相机 / Volume）并读回校验 ----
        client.set_component(camera_entity["index"], camera_entity["generation"],
                             TRANSFORM_TYPE, camera_before)
        camera_restored = read_transform(client, camera_entity)
        camera_restore_ok = camera_restored == camera_before
        report["camera_restored"] = camera_restored
        report["camera_restore_ok"] = camera_restore_ok
        if args.post == "neutral":
            client.set_component(volume_entity["index"], volume_entity["generation"],
                                 VOLUME_TYPE, volume_before)
            volume_restored = read_volume(client, volume_entity)
            volume_restore_ok = volume_restored == volume_before
            report["volume_after"] = volume_restored
            report["volume_restore_ok"] = volume_restore_ok
            diff_fields = [key for key in volume_before
                           if volume_restored.get(key) != volume_before.get(key)]
            report["volume_restore_diff_fields"] = diff_fields
            LOG.info("Volume 恢复: ok=%s (diff=%s)", volume_restore_ok, diff_fields)
            if not volume_restore_ok:
                raise SystemExit(f"Volume 恢复读回不一致: {diff_fields}")
        else:
            report["volume_after"] = read_volume(client, volume_entity)
            LOG.info("--post scene: 不改写 Volume（after 可能被预设 seek 改过曝光/环境光）")
        if not camera_restore_ok:
            raise SystemExit("MainCamera Transform 恢复读回不一致")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - 收尾不掩盖主异常
                LOG.warning("client.close 异常", exc_info=True)
        if args.keep_editor:
            LOG.warning("--keep-editor: 保留编辑器进程 pid=%s", process.pid if process else "?")
        else:
            _stop_editor(process)
        report["editor_kept_alive"] = bool(args.keep_editor)
        time.sleep(0.5)
        if log_file is not None:
            try:
                log_file.close()
            except OSError:
                pass
        leftover = [info for info in read_session_infos()
                    if info["projectPath"] == str(PROJECT_ROOT)]
        report["leftover_sessions"] = leftover
        report["editor"] = {"exe": str(EDITOR_EXE), "log": "editor.log",
                            "startup_seconds": round(startup_seconds, 3)}
        report["editor"].update(count_log_lines(editor_log))
        if leftover and not args.keep_editor:
            LOG.error("收尾后仍有本工程编辑器会话: %s", leftover)

    # ---- 指标 + contact sheet ----
    metrics_started = time.time()
    capture_files: dict[str, dict] = {}
    for item in batch_items:
        capture_files[item["preset"]] = item
    order = [item["preset"] for item in batch_items]
    for record in close_records:
        capture_files[record["leg"]] = record

    legs_report: list[dict] = []
    face_roi_cache: dict[str, tuple[list[bytes], int]] = {}
    for name in order:
        group = preset_group(name)
        if wanted_groups is not None and group not in wanted_groups:
            continue
        item = capture_files[name]
        legs_report.append(measure_leg(
            Path(item["path"]), name, name, group, leg_roi_kind(group),
            args.width, args.height, len(legs_report) + 1, item.get("seq"),
            thumbnails, face_roi_cache))
    for record in close_records:
        entry = measure_leg(out_dir / record["file"], record["leg"], record["preset"], "close",
                            "full_nonsky", args.width, args.height, len(legs_report) + 1,
                            record["seq"], thumbnails, face_roi_cache)
        entry["camera_position"] = record["camera_position"]
        entry["distance_m"] = record["distance_m"]
        legs_report.append(entry)

    report["legs"] = legs_report
    report["face_lr"] = build_face_lr(face_roi_cache, legs_report)
    report["summary"] = build_summary(legs_report, args.post)
    report["post_check"] = build_post_check(legs_report, close_records)
    report["metrics_seconds"] = round(time.time() - metrics_started, 3)

    available = {entry["leg"] for entry in legs_report if entry["file"]}
    layout = contact_layout(available, args.legs, close_ok)
    if layout:
        sheet_path = out_dir / "contact_sheet.png"
        sheet_size = write_contact_sheet(sheet_path, layout, thumbnails)
        report["contact_sheet"] = {
            "path": sheet_path.name,
            "width": sheet_size[0],
            "height": sheet_size[1],
            "columns": 4,
            "thumb": list(CONTACT_THUMB),
            "gap": CONTACT_GAP,
            "contact_sheet_order": [
                {"row": row_index, "cells": row} for row_index, row in enumerate(layout)
            ],
            "note": "face/hair/weapon 三行包含在 36 条矩阵的 9 行内；conditions 行第 4 格留空",
        }
    else:
        report["contact_sheet"] = None
        LOG.warning("无可拼板的 leg，跳过 contact sheet")

    status_after = git_status_lines()
    report["git_status_after"] = status_after
    report["git_status_unexpected"] = [line for line in status_after if line not in status_before]
    report["timings"] = {
        "total_seconds": round(time.time() - started_at, 3),
        "startup_seconds": round(startup_seconds, 3),
        "capture_all_seconds": batch_info.get("elapsed_seconds"),
        "close_seconds": report.get("close_elapsed_seconds"),
        "metrics_seconds": report["metrics_seconds"],
    }

    json_path = out_dir / "eval_metrics.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    log_summary(legs_report, report)
    LOG.info("产物: %s, %s, %s", json_path.name, sheet_path.name, "editor.log")
    if report["git_status_unexpected"]:
        LOG.warning("git status 出现运行前没有的条目（需核查是否资产/meta 被改写）: %s",
                    report["git_status_unexpected"])
    if report["editor"]["error_count"]:
        LOG.error("编辑器日志出现 %d 条 ERROR，退出码 1", report["editor"]["error_count"])
        return 1
    return 0


def measure_leg(path: Path, leg_name: str, preset: str, group: str, roi_kind: str,
                width: int, height: int, index: int, seq: int | None,
                thumbnails: dict[str, list[bytes]],
                face_roi_cache: dict[str, tuple[list[bytes], int]]) -> dict:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    image_w, image_h, channels, rows = decode_png_bytes(data)
    thumbnails[leg_name] = make_thumbnail(rows, channels, image_w, image_h)
    entry: dict = {
        "leg": leg_name,
        "preset": preset,
        "index": index,
        "seq": seq,
        "group": group,
        "kind": "close" if group == "close" else "preset",
        "file": path.name,
        "width": image_w,
        "height": image_h,
        "sha256": sha,
    }
    if roi_kind == "full_nonsky":
        entry["roi"] = "full_nonsky"
        entry.update(measure_close(rows, channels, image_w, image_h))
        return entry
    roi_ref = FACE_ROI_REF if roi_kind == "face" else TORSO_ROI_REF
    roi = scale_roi(roi_ref, image_w, image_h)
    entry["roi"] = roi_kind
    entry["roi_rect"] = list(roi)
    entry.update(measure_roi(rows, channels, roi))
    if roi_kind == "face":
        face_roi_cache[leg_name] = (_roi_rows(rows, channels, roi), channels)
    else:
        entry.pop("shadow_coverage", None)  # 面影覆盖只对 face 组输出
    return entry


def build_face_lr(face_roi_cache: dict[str, tuple[list[bytes], int]],
                  legs: list[dict]) -> dict:
    entry: dict = {"available": False}
    left = face_roi_cache.get("face_left45")
    right = face_roi_cache.get("face_right45")
    if left is None or right is None:
        entry["reason"] = "face_left45/face_right45 未在本轮分析集内"
        return entry
    roi = next((leg["roi_rect"] for leg in legs if leg["leg"] == "face_left45"), None)
    if roi is None:
        entry["reason"] = "face_left45 缺 roi_rect"
        return entry
    diff = face_lr_diff(left[0], right[0], left[1], tuple(roi))
    diff["available"] = True
    diff["baseline_hint"] = {
        "mean_abs_channel_mean_diff_lt": 1.0,
        "frac_gt8_percent_range": [4.0, 6.0],
        "note": "勘察基线（--post scene, 2026-10-31 HEAD）：均值差 <1/255、>8 像素占比 4-6%",
    }
    return diff


def _aggregate(legs: list[dict], key: str) -> dict:
    values = [leg["luma"][key] for leg in legs if leg["luma"][key] is not None]
    if not values:
        return {"count": 0}
    return {"count": len(values), "mean": round(sum(values) / len(values), 6),
            "min": round(min(values), 6), "max": round(max(values), 6)}


def build_summary(legs: list[dict], post: str) -> dict:
    groups = ("body", "face", "hair", "weapon", "condition", "close")
    summary: dict = {"post": post, "legs": len(legs), "groups": {}}
    for group in groups:
        members = [leg for leg in legs if leg["group"] == group]
        if not members:
            continue
        summary["groups"][group] = {
            "legs": len(members),
            "p15": _aggregate(members, "p15"),
            "p50": _aggregate(members, "p50"),
            "p98": _aggregate(members, "p98"),
            "p98_over_p15": _aggregate(members, "p98_over_p15"),
            "clip_frac_mean": round(sum(leg["clip_frac"] for leg in members)
                                    / len(members), 6),
            "dark_order_rgb_legs": sum(1 for leg in members
                                       if leg["dark"]["order"] == "R>G>B"),
        }
    by_camera: dict = {}
    for leg in legs:
        if leg["kind"] != "preset":
            continue
        name = leg["leg"]
        if name in CONDITIONS:
            continue
        camera, _, direction = name.partition("_")
        by_camera.setdefault(camera, {})[direction] = {
            "p15": leg["luma"]["p15"], "p50": leg["luma"]["p50"], "p98": leg["luma"]["p98"],
            "shadow_coverage": leg.get("shadow_coverage"),
        }
    summary["by_camera"] = by_camera
    conditions = {leg["leg"]: {"p15": leg["luma"]["p15"], "p50": leg["luma"]["p50"],
                               "p98": leg["luma"]["p98"]}
                  for leg in legs if leg["group"] == "condition"}
    if conditions:
        summary["conditions"] = conditions
    face_members = [leg for leg in legs if leg["group"] == "face"]
    if face_members:
        summary["face_shadow_coverage"] = {
            leg["leg"]: leg.get("shadow_coverage") for leg in face_members}
    close_members = [leg for leg in legs if leg["group"] == "close"]
    if close_members:
        summary["close"] = {
            leg["leg"]: {"p15": leg["luma"]["p15"], "p50": leg["luma"]["p50"],
                         "p98": leg["luma"]["p98"],
                         "center40_dark_frac": leg["center40"]["dark_frac"],
                         "subject_height_frac": (leg["subject_bbox"] or {}).get("height_frac")}
            for leg in close_members}
    hair = [leg for leg in legs if leg["group"] == "hair"]
    weapon = [leg for leg in legs if leg["group"] == "weapon"]
    if hair and weapon:
        summary["hair_vs_weapon"] = {
            direction: {
                "hair_p50": next((leg["luma"]["p50"] for leg in hair
                                  if leg["leg"] == f"hair_{direction}"), None),
                "weapon_p50": next((leg["luma"]["p50"] for leg in weapon
                                    if leg["leg"] == f"weapon_{direction}"), None),
            }
            for direction in DIRECTIONS
        }
    return summary


def build_post_check(legs: list[dict], close_records: list[dict]) -> dict:
    """close-up 冻帧自检：四张彼此不同、且与对应预设帧不同（帧号 + 哈希双证）。"""
    check: dict = {"close": []}
    sha_by_leg = {leg["leg"]: leg["sha256"] for leg in legs}
    for record in close_records:
        preset_sha = sha_by_leg.get(record["preset"])
        leg_sha = sha_by_leg.get(record["leg"])
        pairwise = {other["leg"]: leg_sha != sha_by_leg.get(other["leg"])
                    for other in close_records if other["leg"] != record["leg"]}
        check["close"].append({
            "leg": record["leg"],
            "seq": record["seq"],
            "preset_leg": record["preset"],
            "distinct_from_preset_capture": (None if preset_sha is None
                                             else leg_sha != preset_sha),
            "distinct_from_other_close": pairwise,
        })
    violations = []
    for item in check["close"]:
        if item["distinct_from_preset_capture"] is False:
            violations.append(f"{item['leg']} == {item['preset_leg']}（冻帧）")
        if any(value is False for value in item["distinct_from_other_close"].values()):
            violations.append(f"{item['leg']} 与其他 close-up 哈希相同（冻帧）")
    seqs = [record["seq"] for record in close_records]
    check["close_seq_unique"] = len(set(seqs)) == len(seqs)
    if not check["close_seq_unique"]:
        violations.append("close-up seq 重复")
    check["violations"] = violations
    check["ok"] = not violations
    if violations:
        raise SystemExit(f"close-up 冻帧自检失败: {violations}")
    return check


def log_summary(legs: list[dict], report: dict) -> None:
    LOG.info("---- leg 指标（%d 条）----", len(legs))
    for leg in legs:
        LOG.info("%-13s %-10s p15=%s p50=%s p98=%s dark=%s order=%s clip=%s%s", leg["leg"],
                 leg["roi"], leg["luma"]["p15"], leg["luma"]["p50"], leg["luma"]["p98"],
                 leg["dark"]["rgb"], leg["dark"]["order"], leg["clip_frac"],
                 "" if leg.get("shadow_coverage") is None
                 else f" shadow={leg['shadow_coverage']}")
    summary = report["summary"]
    for group, stats in summary["groups"].items():
        LOG.info("组 %-10s legs=%s p15=%s p50=%s p98=%s", group, stats["legs"],
                 stats["p15"].get("mean"), stats["p50"].get("mean"), stats["p98"].get("mean"))
    if report.get("face_lr", {}).get("available"):
        lr = report["face_lr"]
        LOG.info("face L/R: mean_abs_diff=%s frac_gt8=%s channel_mean_diff=%s", lr["mean_abs_diff"],
                 lr["frac_gt8"], lr["channel_mean_diff"])
    LOG.info("编辑器 ERROR=%s WARNING=%s", report["editor"]["error_count"],
             report["editor"]["warning_count"])


if __name__ == "__main__":
    raise SystemExit(main())
