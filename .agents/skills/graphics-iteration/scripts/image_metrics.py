#!/usr/bin/env python3
"""确定性图像指标：色彩（OKLCH/CIELAB/ΔE2000）+ 色板匹配 + saliency/构图。

定位：图形迭代的「确定性指标」母本（对应 references/07-metrics.md）。输入为解码后的
sRGB8 像素；只用标准库，颜色计算按 sRGB 原始值，同一输入同一参数逐位复现。
画质模型（MUSIQ/CLIP-IQA）经 `--quality-cmd` 外部命令接入，分数只作参考，验收门由
窗口指标决定。

指标分四组：
  1. tone：luma 分位 / clip / 暗部色序；
  2. color：有效像素的 OKLCH 中位/色相分布、主导色簇、色板覆盖率与 ΔE2000；
  3. saliency：64×64 灰度谱残差（Hou & Zhang 2007）→ 归一化显著图与显著框；
  4. composition：主体框/显著框的占高、占宽、中心偏移、三分线距离与边距。

口径纪律：
  - 颜色计算一律先 sRGB→线性→OKLab/XYZ；统计在 sRGB 值域上给 luma，不混用。
  - `--stride`、saliency 尺寸、掩码、色板阈值都写进输出 `settings`，跨轮只比同口径。
  - 有效像素默认 = 非天空且 luma>floor（与评估脚本一致）；close-up 用 all。
  - 阈值/权重属于 tokens（design tokens），本脚本提供数值，验收结论由 tokens 窗口给出。

用法：
  python .agents/skills/graphics-iteration/scripts/image_metrics.py \
    --image temp/<任务>/capture_front_front_1001.png \
    --tokens <tokens.json> \
    --roi 640,290,1280,580 --stride 1 --out temp/<任务>/metrics_front.json

  # 纯色彩工具：RGB/hex → OKLCH/CIELAB/ΔE
  python .../image_metrics.py --color "#c8a07a" --compare "#b98d6a"

  # 自检（ΔE2000 标准样对 + saliency/合成图回归）
  python .../image_metrics.py --self-test
"""

from __future__ import annotations

import argparse
import cmath
import json
import math
import sys
import time
from pathlib import Path

# 共享 PNG/ROI 工具（同目录，纯标准库）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from png_utils import (  # noqa: E402
    decode_png_bytes, percentile, scale_roi, write_png_rgb,
)

METRICS_VERSION = 1

# 指标口径常量（可按项目重标定）。
CLIP_LEVEL = 250          # clip = 任一通道 >= 250
LUMA_FLOOR = 0.05         # ROI 内排除近黑像素
TORSO_ROI_REF = (640, 290, 1280, 580)   # 参考分辨率下的躯干带示例
FACE_ROI_REF = (820, 330, 1100, 620)    # 参考分辨率下的脸部框示例

# sRGB 常量（IEC 61966-2-1）
_LUMA = (0.2126, 0.7152, 0.0722)
_SKY = ((0.06, "b-r"), (0.70, "b"), (0.47, "r"))  # 天空掩码系数，见 _is_sky

# D65 白点（2°）
_XN, _YN, _ZN = 0.95047, 1.00000, 1.08883
_EPS = 216.0 / 24389.0
_KAPPA = 24389.0 / 27.0

DEFAULT_PALETTE_THRESHOLD = 10.0  # ΔE2000：色板「命中」上限（tokens 可覆盖）
SALIENCY_SIZE = 64


# ======================================================================================
# 色彩科学（纯函数，便于单测）
# ======================================================================================

def srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value: float) -> float:
    return value * 12.92 if value <= 0.0031308 else 1.055 * (value ** (1.0 / 2.4)) - 0.055


def srgb8_to_linear(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return tuple(srgb_to_linear(c / 255.0) for c in rgb)  # type: ignore[return-value]


def linear_to_oklab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1.0 / 3.0), v) for v in (l, m, s))
    return (0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
            1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
            0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_)


def oklab_to_oklch(lab: tuple[float, float, float]) -> tuple[float, float, float]:
    lightness, a, b = lab
    chroma = math.hypot(a, b)
    hue = math.degrees(math.atan2(b, a)) % 360.0
    return (lightness, chroma, hue)


def linear_to_xyz(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb
    return (0.4124564 * r + 0.3575761 * g + 0.1804375 * b,
            0.2126729 * r + 0.7151522 * g + 0.0721750 * b,
            0.0193339 * r + 0.1191920 * g + 0.9503041 * b)


def xyz_to_lab(xyz: tuple[float, float, float]) -> tuple[float, float, float]:
    def f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > _EPS else (_KAPPA * t + 16.0) / 116.0
    fx, fy, fz = f(xyz[0] / _XN), f(xyz[1] / _YN), f(xyz[2] / _ZN)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def srgb8_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return xyz_to_lab(linear_to_xyz(srgb8_to_linear(rgb)))


def srgb8_to_oklch(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return oklab_to_oklch(linear_to_oklab(srgb8_to_linear(rgb)))


def delta_e_2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIEDE2000（Sharma 2005 实现口径，kL=kC=kH=1）。"""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2
    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar = 0.5 * (c1 + c2)
    c7 = c_bar ** 7
    g = 0.5 * (1.0 - math.sqrt(c7 / (c7 + 25.0 ** 7)))
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = math.hypot(a1p, b1)
    c2p = math.hypot(a2p, b2)
    h1p = 0.0 if (a1p == 0.0 and b1 == 0.0) else math.degrees(math.atan2(b1, a1p)) % 360.0
    h2p = 0.0 if (a2p == 0.0 and b2 == 0.0) else math.degrees(math.atan2(b2, a2p)) % 360.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0.0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180.0:
            dhp -= 360.0
        elif dhp < -180.0:
            dhp += 360.0
    dhp_angle = 2.0 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp) / 2.0)

    l_bar = 0.5 * (l1 + l2)
    c_bar_p = 0.5 * (c1p + c2p)
    if c1p * c2p == 0.0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180.0:
        h_bar = 0.5 * (h1p + h2p)
    elif h1p + h2p < 360.0:
        h_bar = 0.5 * (h1p + h2p + 360.0)
    else:
        h_bar = 0.5 * (h1p + h2p - 360.0)

    t = (1.0 - 0.17 * math.cos(math.radians(h_bar - 30.0))
         + 0.24 * math.cos(math.radians(2.0 * h_bar))
         + 0.32 * math.cos(math.radians(3.0 * h_bar + 6.0))
         - 0.20 * math.cos(math.radians(4.0 * h_bar - 63.0)))
    d_theta = 30.0 * math.exp(-(((h_bar - 275.0) / 25.0) ** 2))
    c7_bar = c_bar_p ** 7
    r_c = 2.0 * math.sqrt(c7_bar / (c7_bar + 25.0 ** 7))
    s_l = 1.0 + (0.015 * (l_bar - 50.0) ** 2) / math.sqrt(20.0 + (l_bar - 50.0) ** 2)
    s_c = 1.0 + 0.045 * c_bar_p
    s_h = 1.0 + 0.015 * c_bar_p * t
    r_t = -math.sin(math.radians(2.0 * d_theta)) * r_c
    return math.sqrt((dlp / s_l) ** 2 + (dcp / s_c) ** 2 + (dhp_angle / s_h) ** 2
                     + r_t * (dcp / s_c) * (dhp_angle / s_h))


def parse_hex_color(text: str) -> tuple[int, int, int]:
    value = text.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"不是 6 位 hex 颜色: {text}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def format_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


# ======================================================================================
# 掩码 / 采样
# ======================================================================================

def is_sky(r: int, g: int, b: int) -> bool:
    """天空掩码：b-r>0.06*255 && b>0.70*255 && r>0.47*255。"""
    return (b - r) > 0.06 * 255.0 and b > 0.70 * 255.0 and r > 0.47 * 255.0


def luma8(r: int, g: int, b: int) -> float:
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def resolve_roi(roi: str | None, width: int, height: int) -> tuple[int, int, int, int]:
    if not roi or roi == "torso":
        return scale_roi(TORSO_ROI_REF, width, height)
    if roi == "face":
        return scale_roi(FACE_ROI_REF, width, height)
    if roi == "full":
        return (0, 0, width, height)
    parts = [int(value) for value in roi.split(",")]
    if len(parts) != 4:
        raise ValueError(f"--roi 需 x0,y0,x1,y1 或 torso/face/full: {roi}")
    return tuple(parts)  # type: ignore[return-value]


def _percentile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    return percentile(values, q)


# ======================================================================================
# 显著性（64×64 谱残差）
# ======================================================================================

def _fft(values: list[complex]) -> list[complex]:
    """迭代基 2 FFT（长度必须为 2 的幂）。"""
    n = len(values)
    if n & (n - 1):
        raise ValueError("FFT 长度必须是 2 的幂")
    out = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            out[i], out[j] = out[j], out[i]
    length = 2
    while length <= n:
        angle = -2.0 * math.pi / length
        w_len = cmath.exp(complex(0.0, angle))
        half = length // 2
        for start in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                even = out[start + k]
                odd = out[start + k + half] * w
                out[start + k] = even + odd
                out[start + k + half] = even - odd
                w *= w_len
        length <<= 1
    return out


def _ifft(values: list[complex]) -> list[complex]:
    n = len(values)
    conj = [value.conjugate() for value in values]
    transformed = _fft(conj)
    return [value.conjugate() / n for value in transformed]


def _fft2(grid: list[list[complex]]) -> list[list[complex]]:
    rows = [_fft(row) for row in grid]
    cols = [[rows[y][x] for y in range(len(rows))] for x in range(len(rows[0]))]
    cols = [_fft(col) for col in cols]
    return [[cols[x][y] for x in range(len(cols))] for y in range(len(rows))]


def _ifft2(grid: list[list[complex]]) -> list[list[complex]]:
    rows = [_ifft(row) for row in grid]
    cols = [[rows[y][x] for y in range(len(rows))] for x in range(len(rows[0]))]
    cols = [_ifft(col) for col in cols]
    return [[cols[x][y] for x in range(len(cols))] for y in range(len(rows))]


def _blur3(grid: list[list[float]]) -> list[list[float]]:
    height = len(grid)
    width = len(grid[0])
    out = [[0.0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            total = 0.0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    total += grid[(y + dy) % height][(x + dx) % width]
            out[y][x] = total / 9.0
    return out


def spectral_saliency(gray: list[list[float]], size: int = SALIENCY_SIZE) -> list[list[float]]:
    """谱残差显著图（[0,1]，与输入同尺寸；65536 像素级纯 Python 可接受）。"""
    height = len(gray)
    width = len(gray[0])
    max_cols = max(1, min(size, width))
    max_rows = max(1, min(size, height))
    cols = 1 << int(math.floor(math.log2(max_cols)))
    rows = 1 << int(math.floor(math.log2(max_rows)))
    if cols < 1:
        cols = 1
    if rows < 1:
        rows = 1
    small: list[list[float]] = []
    for j in range(rows):
        y0 = j * height // rows
        y1 = max(y0 + 1, (j + 1) * height // rows)
        line = []
        for i in range(cols):
            x0 = i * width // cols
            x1 = max(x0 + 1, (i + 1) * width // cols)
            total = 0.0
            count = 0
            for y in range(y0, y1):
                row = gray[y]
                for x in range(x0, x1):
                    total += row[x]
                    count += 1
            line.append(total / count if count else 0.0)
        small.append(line)

    spectrum = _fft2([[complex(value, 0.0) for value in line] for line in small])
    log_amp = [[math.log(abs(value) + 1e-8) for value in line] for line in spectrum]
    smooth = _blur3(log_amp)
    residual = [[log_amp[y][x] - smooth[y][x] for x in range(cols)] for y in range(rows)]
    recon = [[cmath.exp(complex(residual[y][x], cmath.phase(spectrum[y][x])))
              for x in range(cols)] for y in range(rows)]
    inverse = _ifft2(recon)
    sal = [[abs(value) ** 2 for value in line] for line in inverse]
    sal = _blur3(sal)
    values = [value for line in sal for value in line]
    low, high = min(values), max(values)
    span = high - low
    small_sal = [[(value - low) / span if span > 1e-12 else 0.0 for value in line] for line in sal]

    # 双线性回放到输入尺寸（确定性；显著图只用于框/统计）
    out: list[list[float]] = []
    for y in range(height):
        gy = (y + 0.5) * rows / height - 0.5
        y0 = max(0, min(rows - 1, int(math.floor(gy))))
        y1 = min(rows - 1, y0 + 1)
        fy = gy - y0
        line = []
        for x in range(width):
            gx = (x + 0.5) * cols / width - 0.5
            x0 = max(0, min(cols - 1, int(math.floor(gx))))
            x1 = min(cols - 1, x0 + 1)
            fx = gx - x0
            top = small_sal[y0][x0] * (1 - fx) + small_sal[y0][x1] * fx
            bottom = small_sal[y1][x0] * (1 - fx) + small_sal[y1][x1] * fx
            line.append(top * (1 - fy) + bottom * fy)
        out.append(line)
    return out


def saliency_bbox(sal: list[list[float]], sigma: float = 1.0) -> dict:
    """显著框 = 阈值以上「总显著质量最大」的 4 邻域连通块（避免边框伪影拉大框）。"""
    height = len(sal)
    width = len(sal[0])
    flat = [value for line in sal for value in line]
    mean = sum(flat) / len(flat)
    var = sum((value - mean) ** 2 for value in flat) / len(flat)
    if math.sqrt(var) < 1e-9:
        return {"threshold": round(mean, 6), "pixels": 0, "bbox": None,
                "centroid": None, "mass_frac": 0.0}
    threshold = min(0.95, mean + sigma * math.sqrt(var))
    total_mass = sum(flat)
    seen = [[False] * width for _ in range(height)]
    best = None
    for y0 in range(height):
        for x0 in range(width):
            if seen[y0][x0] or sal[y0][x0] < threshold:
                continue
            stack = [(x0, y0)]
            seen[y0][x0] = True
            min_x, min_y, max_x, max_y = x0, y0, x0, y0
            mass = 0.0
            count = 0
            sum_x = sum_y = 0.0
            while stack:
                x, y = stack.pop()
                value = sal[y][x]
                mass += value
                count += 1
                sum_x += x * value
                sum_y += y * value
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height and not seen[ny][nx] \
                            and sal[ny][nx] >= threshold:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            if best is None or mass > best["mass"]:
                best = {"mass": mass, "count": count, "bbox": (min_x, min_y, max_x, max_y),
                        "centroid": (sum_x / mass / width, sum_y / mass / height)}
    if best is None:
        return {"threshold": round(threshold, 6), "pixels": 0, "bbox": None,
                "centroid": None, "mass_frac": 0.0}
    min_x, min_y, max_x, max_y = best["bbox"]
    return {
        "threshold": round(threshold, 6),
        "pixels": best["count"],
        "mass_frac": round(best["mass"] / max(1e-12, total_mass), 6),
        "bbox": {"x0": min_x, "y0": min_y, "x1": max_x, "y1": max_y,
                 "width": max_x - min_x + 1, "height": max_y - min_y + 1},
        "centroid": [round(best["centroid"][0], 6), round(best["centroid"][1], 6)],
    }


# ======================================================================================
# 构图
# ======================================================================================

def composition(bbox: dict | None, width: int, height: int) -> dict | None:
    if not bbox:
        return None
    x0, y0 = bbox["x0"], bbox["y0"]
    x1, y1 = bbox["x1"] + 1, bbox["y1"] + 1
    bw, bh = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2.0 / width
    cy = (y0 + y1) / 2.0 / height
    thirds = min(math.hypot(cx - tx, cy - ty) for tx in (1 / 3, 2 / 3) for ty in (1 / 3, 2 / 3))
    return {
        "width_frac": round(bw / width, 6),
        "height_frac": round(bh / height, 6),
        "area_frac": round(bw * bh / float(width * height), 6),
        "center": [round(cx, 6), round(cy, 6)],
        "center_offset": round(math.hypot(cx - 0.5, cy - 0.5) * 2.0, 6),
        "thirds_distance": round(thirds, 6),
        "headroom_frac": round(y0 / height, 6),
        "bottom_margin_frac": round(1.0 - y1 / height, 6),
        "left_margin_frac": round(x0 / width, 6),
        "right_margin_frac": round(1.0 - x1 / width, 6),
    }


# ======================================================================================
# 色板
# ======================================================================================

def token_palette(tokens: dict) -> list[dict]:
    """从 design tokens 提取 palette：约定 `color.palette.<name>` 为 color token。

    支持 DTCG color 对象（colorSpace/components/hex）。解析器由 design_tokens.py 提供；
    这里接受已扁平化+解析后的 `{"colors": [{"path","hex","components"}]}`，避免重复实现。
    """
    colors = []
    for entry in tokens.get("colors", []):
        rgb = tuple(entry.get("components", [])[:3])
        if len(rgb) != 3:
            if entry.get("hex"):
                rgb = parse_hex_color(entry["hex"])
        colors.append({"path": entry["path"], "name": entry.get("name", entry["path"]),
                       "hex": format_hex(rgb), "rgb": list(rgb)})
    return colors


def _nearest_token(palette: list[dict], lab: tuple[float, float, float]) -> tuple[int, float]:
    best = None
    best_distance = float("inf")
    for index, color in enumerate(palette):
        distance = delta_e_2000(lab, color["lab"])
        if distance < best_distance:
            best_distance = distance
            best = index
    return (best if best is not None else -1), best_distance


def palette_metrics(samples: list[tuple[int, int, int]], palette: list[dict],
                    threshold: float = DEFAULT_PALETTE_THRESHOLD) -> dict | None:
    if not palette or not samples:
        return None
    for color in palette:
        color.setdefault("lab", srgb8_to_lab(tuple(color["rgb"])))
    counts = [0] * len(palette)
    sums = [0.0] * len(palette)
    distances: list[float] = []
    per_token_total = [0.0] * len(palette)
    for rgb in samples:
        lab = srgb8_to_lab(rgb)
        index, distance = _nearest_token(palette, lab)
        distances.append(distance)
        if index >= 0:
            per_token_total[index] += 1
            sums[index] += distance
            if distance <= threshold:
                counts[index] += 1
    distances.sort()
    covered = sum(counts)
    per_token = []
    for index, color in enumerate(palette):
        hits = counts[index]
        per_token.append({
            "path": color["path"],
            "hex": color["hex"],
            "pixels": hits,
            "coverage": round(hits / len(samples), 6),
            "mean_delta_e": round(sums[index] / per_token_total[index], 4)
            if per_token_total[index] else None,
        })
    return {
        "threshold_delta_e": threshold,
        "palette_size": len(palette),
        "sample_count": len(samples),
        "coverage": round(covered / len(samples), 6),
        "mean_delta_e": round(sum(distances) / len(distances), 4),
        "p90_delta_e": round(percentile(distances, 90.0), 4),
        "p99_delta_e": round(percentile(distances, 99.0), 4),
        "tokens": per_token,
        "unmatched_frac": round(1.0 - covered / len(samples), 6),
    }


def dominant_colors(samples: list[tuple[int, int, int]], limit: int = 8) -> list[dict]:
    """固定 8×8×8 OKLab 桶 + 桶内均值：以确定性桶均值替代随机 k-means。"""
    bins: dict[tuple[int, int, int], list] = {}
    for rgb in samples:
        lightness, a, b = linear_to_oklab(srgb8_to_linear(rgb))
        key = (min(7, max(0, int((lightness + 0.05) / 1.05 * 8))),
               min(7, max(0, int((a + 0.4) / 0.8 * 8))),
               min(7, max(0, int((b + 0.4) / 0.8 * 8))))
        bucket = bins.setdefault(key, [0, 0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += rgb[0]
        bucket[2] += rgb[1]
        bucket[3] += rgb[2]
    ordered = sorted(bins.values(), key=lambda item: item[0], reverse=True)[:limit]
    total = sum(item[0] for item in bins.values()) or 1
    out = []
    for count, red, green, blue in ordered:
        rgb = (int(round(red / count)), int(round(green / count)), int(round(blue / count)))
        lightness, chroma, hue = srgb8_to_oklch(rgb)
        out.append({"hex": format_hex(rgb), "share": round(count / total, 4),
                    "oklch": [round(lightness, 4), round(chroma, 4), round(hue, 1)]})
    return out


# ======================================================================================
# 单图分析
# ======================================================================================

def analyze_pixels(width: int, height: int, channels: int, rows: list[bytes],
                   palette: list[dict] | None = None, roi: tuple[int, int, int, int] | None = None,
                   mask: str = "nonsky", stride: int = 2,
                   palette_threshold: float = DEFAULT_PALETTE_THRESHOLD) -> dict:
    x0, y0, x1, y1 = roi or (0, 0, width, height)
    stride = max(1, stride)
    lums: list[float] = []
    color_samples: list[tuple[int, int, int]] = []
    clipped = 0
    sky = 0
    total = 0
    bbox = None
    min_x, min_y, max_x, max_y = width, height, -1, -1
    oklch_l: list[float] = []
    oklch_c: list[float] = []
    oklch_h: list[float] = []
    hue_sin = 0.0
    hue_cos = 0.0

    for y in range(y0, y1, stride):
        row = rows[y]
        for x in range(x0, x1, stride):
            index = x * channels
            r, g, b = row[index], row[index + 1], row[index + 2]
            total += 1
            sky_pixel = is_sky(r, g, b)
            if sky_pixel:
                sky += 1
            value = luma8(r, g, b)
            if mask == "nonsky" and (sky_pixel or value <= LUMA_FLOOR):
                continue
            if mask == "subject" and value <= LUMA_FLOOR:
                continue
            lums.append(value)
            color_samples.append((r, g, b))
            if r >= CLIP_LEVEL or g >= CLIP_LEVEL or b >= CLIP_LEVEL:
                clipped += 1
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            if (x - x0) % (stride * 2) == 0 and (y - y0) % (stride * 2) == 0:
                lightness, chroma, hue = srgb8_to_oklch((r, g, b))
                oklch_l.append(lightness)
                oklch_c.append(chroma)
                oklch_h.append(hue)
                hue_radians = math.radians(hue)
                hue_sin += math.sin(hue_radians)
                hue_cos += math.cos(hue_radians)

    subject_bbox = None
    if max_x >= 0:
        subject_bbox = {"x0": min_x, "y0": min_y, "x1": max_x, "y1": max_y,
                        "width": max_x - min_x + 1, "height": max_y - min_y + 1}

    # 显著图：需要整幅 luma（在采样图上算，保持双线性回放的确定性）
    gray: list[list[float]] = []
    for y in range(0, height, stride):
        line = []
        for x in range(0, width, stride):
            index = x * channels
            line.append(luma8(rows[y][index], rows[y][index + 1], rows[y][index + 2]))
        gray.append(line)
    sal = spectral_saliency(gray, SALIENCY_SIZE)
    sal_info = saliency_bbox(sal)
    sal_grid = (len(gray[0]), len(gray))

    result: dict = {
        "size": [width, height],
        "roi": [x0, y0, x1, y1],
        "mask": mask,
        "stride": stride,
        "pixel_samples": total,
        "valid_pixels": len(lums),
        "valid_frac": round(len(lums) / total, 6) if total else None,
        "sky_frac": round(sky / total, 6) if total else None,
        "luma": {
            "p15": round(percentile(sorted(lums), 15.0), 6) if lums else None,
            "p50": round(percentile(sorted(lums), 50.0), 6) if lums else None,
            "p98": round(percentile(sorted(lums), 98.0), 6) if lums else None,
            "p98_over_p15": None,
            "mean": round(sum(lums) / len(lums), 6) if lums else None,
        },
        "clip_frac": round(clipped / len(lums), 6) if lums else None,
        "color": None,
        "palette": None,
        "dominant_colors": None,
        "subject_bbox": subject_bbox,
        "subject_composition": composition(subject_bbox, width, height),
        "saliency": sal_info,
        "saliency_composition": composition(sal_info.get("bbox"), sal_grid[0], sal_grid[1]),
    }
    if len(lums) >= 2 and result["luma"]["p15"]:
        result["luma"]["p98_over_p15"] = round(result["luma"]["p98"] / result["luma"]["p15"], 4)
    if oklch_l:
        result["color"] = {
            "oklch_p50": [round(percentile(sorted(oklch_l), 50.0), 4),
                          round(percentile(sorted(oklch_c), 50.0), 4),
                          round(percentile(sorted(oklch_h), 50.0), 1)],
            "oklch_mean": [round(sum(oklch_l) / len(oklch_l), 4),
                           round(sum(oklch_c) / len(oklch_c), 4),
                           round(math.degrees(math.atan2(hue_sin, hue_cos)) % 360.0, 1)],
            "hue_circular": {"sin": round(hue_sin, 6), "cos": round(hue_cos, 6)},
            "chroma_p90": round(percentile(sorted(oklch_c), 90.0), 4),
            "dark_chroma_p50": None,
        }
        if palette:
            result["palette"] = palette_metrics(color_samples, palette, palette_threshold)
        result["dominant_colors"] = dominant_colors(color_samples)
    return result


def analyze_file(path: Path, palette: list[dict] | None = None, roi: str | None = None,
                 mask: str = "nonsky", stride: int = 2,
                 palette_threshold: float = DEFAULT_PALETTE_THRESHOLD) -> dict:
    data = path.read_bytes()
    width, height, channels, rows = decode_png_bytes(data)
    rect = resolve_roi(roi, width, height)
    result = analyze_pixels(width, height, channels, rows, palette, rect, mask, stride,
                            palette_threshold)
    result["file"] = str(path)
    result["metrics_version"] = METRICS_VERSION
    result["definitions"] = {
        "luma": "0.2126*r + 0.7152*g + 0.0722*b (sRGB 0..1)",
        "color": "sRGB8 -> linear -> OKLab/OKLCH；D65 CIELAB",
        "delta_e": "CIEDE2000 (kL=kC=kH=1)",
        "palette_coverage": f"有效像素最近 token 的 ΔE2000 <= {palette_threshold} 的占比",
        "saliency": "64x64 灰度谱残差 + 3x3 模糊，阈值 mean+1σ，回放双线性",
        "composition": "bbox 归一化到 [0,1]；thirds_distance = 中心到最近三分点的归一化距离",
        "mask": "nonsky = 非天空且 luma>0.05；subject = luma>0.05；all = 全 ROI",
        "stride": "采样步长（luma/色板逐像素，OKLCH 统计再隔点采样）",
    }
    return result


# ======================================================================================
# 自检与 CLI
# ======================================================================================

def _self_test() -> int:
    failures: list[str] = []

    # Sharma CIEDE2000 标准样对（kL=kC=kH=1）
    pairs = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
        ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ]
    for lab1, lab2, expected in pairs:
        got = delta_e_2000(lab1, lab2)
        if abs(got - expected) > 1e-3:
            failures.append(f"ΔE2000 {lab1} vs {lab2}: {got:.4f} != {expected}")

    # 色彩往返：sRGB→OKLCH；白/黑端点必须落 L=1/0、C≈0
    for rgb in [(0, 0, 0), (255, 255, 255), (200, 160, 120), (12, 200, 40)]:
        lch = oklab_to_oklch(linear_to_oklab(srgb8_to_linear(rgb)))
        if rgb == (255, 255, 255) and (abs(lch[0] - 1.0) > 1e-3 or lch[1] > 1e-3):
            failures.append(f"white OKLCH 异常: {lch}")
        if rgb == (0, 0, 0) and (abs(lch[0]) > 1e-3 or lch[1] > 1e-3):
            failures.append(f"black OKLCH 异常: {lch}")

    # saliency：中心亮块应把显著框拉到中心 1/3 区域
    size = 48
    gray = [[0.02] * size for _ in range(size)]
    for y in range(size // 3, 2 * size // 3):
        for x in range(size // 3, 2 * size // 3):
            gray[y][x] = 0.9
    sal = spectral_saliency(gray, 32)
    info = saliency_bbox(sal)
    if not info["bbox"]:
        failures.append("saliency 无显著框")
    else:
        cx = (info["bbox"]["x0"] + info["bbox"]["x1"]) / 2 / size
        cy = (info["bbox"]["y0"] + info["bbox"]["y1"]) / 2 / size
        if abs(cx - 0.5) > 0.2 or abs(cy - 0.5) > 0.2:
            failures.append(f"saliency 中心偏移: ({cx:.2f},{cy:.2f})")

    # 端到端：合成两色 PNG → 解码 → 色板覆盖率 / 构图
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "half.png"
        width = height = 40
        left = (240, 198, 180)
        right = (120, 90, 80)
        rows = []
        for _y in range(height):
            line = bytearray()
            for x in range(width):
                line.extend(left if x < width // 2 else right)
            rows.append(bytes(line))
        write_png_rgb(path, width, height, rows)
        palette = [
            {"path": "color.palette.a", "name": "a", "hex": format_hex(left),
             "rgb": list(left)},
            {"path": "color.palette.b", "name": "b", "hex": format_hex(right),
             "rgb": list(right)},
        ]
        result = analyze_file(path, palette, roi="full", mask="all", stride=1,
                              palette_threshold=2.0)
        if result["palette"]["coverage"] < 0.99:
            failures.append(f"合成图色板覆盖率异常: {result['palette']['coverage']}")
        shares = [entry["coverage"] for entry in result["palette"]["tokens"]]
        if not all(0.45 <= share <= 0.55 for share in shares):
            failures.append(f"两色占比异常: {shares}")
        if result["luma"]["p50"] is None:
            failures.append("合成图 luma 缺失")

    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}", file=sys.stderr)
        return 1
    print(f"[OK] image_metrics self-test: ΔE2000 样对 {len(pairs)} 组、OKLCH、saliency 全过")
    return 0


def _color_report(color: str, compare: str | None) -> dict:
    rgb = parse_hex_color(color)
    lab = srgb8_to_lab(rgb)
    light, chroma, hue = srgb8_to_oklch(rgb)
    report: dict = {
        "hex": format_hex(rgb),
        "srgb": list(rgb),
        "linear": [round(value, 6) for value in srgb8_to_linear(rgb)],
        "oklab": [round(value, 6) for value in linear_to_oklab(srgb8_to_linear(rgb))],
        "oklch": [round(light, 6), round(chroma, 6), round(hue, 2)],
        "lab_d65": [round(value, 4) for value in lab],
    }
    if compare:
        rgb2 = parse_hex_color(compare)
        report["compare"] = {
            "hex": format_hex(rgb2),
            "delta_e_2000": round(delta_e_2000(lab, srgb8_to_lab(rgb2)), 4),
            "delta_e_ok": round(math.dist(
                linear_to_oklab(srgb8_to_linear(rgb)),
                linear_to_oklab(srgb8_to_linear(rgb2))) * 100.0, 4),
        }
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="确定性图像指标：色彩/色板/显著性/构图")
    parser.add_argument("--image", type=Path, help="PNG 路径（8bit 非隔行）")
    parser.add_argument("--tokens", type=Path, help="design tokens（提供 color.palette）")
    parser.add_argument("--roi", default=None, help="x0,y0,x1,y1 或 torso/face/full（缺省 torso）")
    parser.add_argument("--mask", choices=("nonsky", "subject", "all"), default="nonsky")
    parser.add_argument("--stride", type=int, default=2, help="采样步长（缺省 2；逐像素用 1）")
    parser.add_argument("--palette-threshold", type=float, default=DEFAULT_PALETTE_THRESHOLD,
                        help="色板命中 ΔE2000 阈值（缺省 10）")
    parser.add_argument("--out", type=Path, help="输出 JSON（缺省 stdout）")
    parser.add_argument("--quality-cmd",
                        help="外部画质模型命令，含 {image} 占位；stdout 输出 "
                             "{\"model\":..., \"score\":...}（参考项，不作门）")
    parser.add_argument("--quality-timeout", type=float, default=120.0)
    parser.add_argument("--color", help="只算颜色：hex/rgb（如 #c8a07a）")
    parser.add_argument("--compare", help="与另一个颜色比较 ΔE2000")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def run_quality_command(command: str, image: Path, timeout: float) -> dict:
    import subprocess
    started = time.time()
    try:
        completed = subprocess.run(command.format(image=str(image)), shell=True,
                                   capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"超时 {timeout}s"}
    if completed.returncode != 0:
        return {"ok": False, "error": f"rc={completed.returncode}",
                "stderr": completed.stderr[-1000:]}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"stdout 不是 JSON: {exc}",
                "stdout": completed.stdout[-1000:]}
    payload["ok"] = True
    payload["duration"] = round(time.time() - started, 3)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.color:
        report = _color_report(args.color, args.compare)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if not args.image:
        raise SystemExit("需要 --image（或用 --color/--self-test）")
    palette = None
    if args.tokens:
        from design_tokens import load_tokens  # 同目录；避免顶层循环依赖
        palette = load_tokens(args.tokens)["palette"]
    result = analyze_file(args.image, palette, args.roi, args.mask, args.stride,
                          args.palette_threshold)
    if args.quality_cmd:
        result["quality"] = run_quality_command(args.quality_cmd, args.image,
                                                args.quality_timeout)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"[image_metrics] 写入 {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
