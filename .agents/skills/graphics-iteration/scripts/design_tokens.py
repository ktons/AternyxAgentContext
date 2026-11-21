#!/usr/bin/env python3
"""DTCG design tokens：解析 / 校验 / 色板导出（美术规范的单一事实源）。

定位：图形迭代的「设计 token」母本（对应 references/06-design-tokens.md）。实现
W3C Design Tokens Community Group Format 2025.10 的可落地子集：

  - 组（无 `$value`）与 token（含 `$value`）；`$type` 可写在组上继承；
  - 别名 `{group.token}`（含 `$root` 路径）、链式引用与循环检测；
  - 已验证类型：color / number / dimension / duration / fontFamily / fontWeight /
    cubicBezier；其余类型原样保留只做警告；
  - color 值支持 DTCG 对象 `{colorSpace, components, alpha?, hex?}`（本仓库只消费 srgb）。

约定（本仓库的「美术规范」投影，不是 DTCG 强制）：
  - `color.palette.<name>`：色板 token（镜头/材质必须能落到的颜色）；
  - `lookdev.window.<profile>.<group>.<metric>`：验收窗口（number）；
  - `lookdev.weight.<group>.<metric>`：复合分权重（number，缺省 1）；
  - `lookdev.paletteThreshold`：色板 ΔE2000 命中阈值（number，缺省 10）；
  - `quality.<model>.*`：画质模型基线/容差（只作参考，不作硬门）。

校验只回答「token 文件自身是否自洽、是否可被脚本消费」，不判断艺术好坏；艺术判断
仍在 reference/审阅。口径变更（窗口、阈值、权重）必须改 token 文件并记录版本，不能
在脚本里写死第二份。

用法：
  python .agents/skills/graphics-iteration/scripts/design_tokens.py lint \
      Test/Projects/General/Fixtures/lookdev/art.tokens.json
  python .../design_tokens.py resolve <file> --out temp/tokens.resolved.json
  python .../design_tokens.py palette <file>
  python .../design_tokens.py check-image <file> --image <png> [--roi torso] [--out json]
  python .../design_tokens.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import image_metrics as im  # noqa: E402

TOKENS_VERSION = 1
KNOWN_TYPES = {
    "color", "number", "dimension", "duration", "fontFamily", "fontWeight",
    "cubicBezier", "strokeStyle", "border", "transition", "shadow", "gradient",
    "typography",
}
ALIAS_RE = re.compile(r"^\{([^{}]+)\}$")
FORBIDDEN_NAME_CHARS = set(".{}")
DEFAULT_PALETTE_THRESHOLD = im.DEFAULT_PALETTE_THRESHOLD
COMPOSITION_METRICS = {
    "p15Min", "p15Max", "p50Min", "p50Max", "p98Min", "p98Max",
    "ratioMin", "ratioMax", "clipMax", "shadowCoverageMin", "shadowCoverageMax",
    "paletteCoverageMin", "paletteDeltaEMax",
    "subjectHeightMin", "subjectHeightMax", "subjectWidthMin", "subjectWidthMax",
    "centerOffsetMax", "thirdsDistanceMax", "headroomMin", "headroomMax",
}


class TokenError(ValueError):
    pass


# ======================================================================================
# 解析
# ======================================================================================

def _join(prefix: list[str], name: str) -> str:
    return ".".join(prefix + [name])


def _check_name(name: str, path: str) -> list[str]:
    problems = []
    if name.startswith("$") and name != "$root":
        problems.append(f"{path}: token/组名不能以 $ 开头（$root 除外）")
    bad = FORBIDDEN_NAME_CHARS & set(name)
    if bad:
        problems.append(f"{path}: 名称含保留字符 {''.join(sorted(bad))}")
    return problems


def _walk(node: dict, prefix: list[str], inherited_type: str | None,
          out: dict[str, dict], errors: list[str], warnings: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append(f"{'.'.join(prefix) or '<root>'}: 组必须是 JSON 对象")
        return
    if "$value" in node:
        path = ".".join(prefix)
        token_type = node.get("$type", inherited_type)
        if not token_type:
            errors.append(f"{path}: 无法确定 $type（本 token 与父组都未声明）")
            token_type = "unknown"
        elif token_type not in KNOWN_TYPES:
            warnings.append(f"{path}: 未知 $type '{token_type}'（原样保留）")
        out[path] = {
            "type": token_type,
            "raw": node["$value"],
            "alias": isinstance(node["$value"], str) and bool(ALIAS_RE.match(node["$value"].strip())),
            "description": node.get("$description"),
            "extensions": node.get("$extensions") or {},
        }
        return
    group_type = node.get("$type", inherited_type)
    for key, value in node.items():
        if key.startswith("$") and key != "$root":
            continue  # $description/$type/$extensions/$deprecated/$extends
        if not isinstance(value, dict):
            errors.append(f"{_join(prefix, key)}: 既不是组（对象）也不是 token（含 $value）")
            continue
        errors.extend(_check_name(key, _join(prefix, key)))
        _walk(value, prefix + [key], group_type, out, errors, warnings)


def _resolve(raw, path: str, tokens: dict[str, dict], stack: list[str],
             errors: list[str], cache: dict[str, object]):
    if isinstance(raw, str):
        match = ALIAS_RE.match(raw.strip())
        if not match:
            return raw
        target = match.group(1).strip()
        if target in stack:
            errors.append(f"{path}: 别名循环 {' -> '.join(stack + [target])}")
            return raw
        if target not in tokens:
            errors.append(f"{path}: 别名目标不存在 '{target}'")
            return raw
        if target in cache:
            return cache[target]
        value = _resolve(tokens[target]["raw"], target, tokens, stack + [target], errors, cache)
        cache[target] = value
        return value
    if isinstance(raw, dict):
        return {key: _resolve(value, f"{path}.{key}", tokens, stack, errors, cache)
                for key, value in raw.items()}
    if isinstance(raw, list):
        return [_resolve(value, f"{path}[{index}]", tokens, stack, errors, cache)
                for index, value in enumerate(raw)]
    return raw


def _color_rgb(value: dict, path: str, errors: list[str]) -> tuple[tuple[int, int, int], float] | None:
    if not isinstance(value, dict) or "components" not in value:
        errors.append(f"{path}: color 值必须是 {{colorSpace, components}} 对象")
        return None
    space = value.get("colorSpace", "srgb")
    if space not in ("srgb", "srgb-linear"):
        errors.append(f"{path}: 暂不支持 colorSpace '{space}'（只支持 srgb/srgb-linear）")
        return None
    components = value.get("components")
    if not isinstance(components, list) or len(components) != 3:
        errors.append(f"{path}: color.components 必须是 3 个数字")
        return None
    normalized = []
    for index, component in enumerate(components):
        if not isinstance(component, (int, float)) or isinstance(component, bool):
            errors.append(f"{path}: color.components[{index}] 不是数字")
            return None
        normalized.append(float(component))
    if space == "srgb-linear":
        normalized = [im.linear_to_srgb(max(0.0, min(1.0, c))) for c in normalized]
    if any(c < -1e-6 or c > 1 + 1e-6 for c in normalized):
        errors.append(f"{path}: sRGB 分量越界 {normalized}")
        return None
    rgb = tuple(max(0, min(255, int(round(c * 255.0)))) for c in normalized)
    alpha = value.get("alpha", 1.0)
    if not isinstance(alpha, (int, float)) or isinstance(alpha, bool) or not 0 <= alpha <= 1:
        errors.append(f"{path}: color.alpha 必须是 0..1 数字")
        alpha = 1.0
    return rgb, float(alpha)


def _validate_value(path: str, token: dict, errors: list[str], warnings: list[str]) -> object:
    value = token["value"]
    token_type = token["type"]
    if token_type == "color":
        parsed = _color_rgb(value, path, errors)
        if parsed is None:
            return value
        rgb, alpha = parsed
        hex_value = (value.get("hex") or "").lower() if isinstance(value, dict) else ""
        if not hex_value:
            token["hex"] = im.format_hex(rgb)
        else:
            expected = tuple(int(hex_value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            if max(abs(expected[i] - rgb[i]) for i in range(3)) > 1:
                warnings.append(
                    f"{path}: hex {hex_value} 与 components 不一致（按 components 消费，"
                    f"建议同步为 {im.format_hex(rgb)}）")
            token["hex"] = hex_value
        token["rgb"] = list(rgb)
        token["alpha"] = alpha
        token["oklch"] = [round(v, 6) for v in im.srgb8_to_oklch(rgb)]
        extension = (token.get("extensions") or {}).get("aternyx", {})
        declared = extension.get("oklch") if isinstance(extension, dict) else None
        if declared is not None:
            if (not isinstance(declared, list) or len(declared) != 3
                    or any(not isinstance(v, (int, float)) for v in declared)):
                warnings.append(f"{path}: $extensions.aternyx.oklch 必须是 [L, C, H]")
            else:
                computed = token["oklch"]
                if (abs(declared[0] - computed[0]) > 0.01
                        or abs(declared[1] - computed[1]) > 0.006
                        or abs((declared[2] - computed[2] + 180) % 360 - 180) > 1.0):
                    warnings.append(
                        f"{path}: 声明的 OKLCH {declared} 与 sRGB 计算值 {computed} 不一致")
    elif token_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            errors.append(f"{path}: number 值不是数字")
    elif token_type == "dimension":
        if (not isinstance(value, dict) or not isinstance(value.get("value"), (int, float))
                or not isinstance(value.get("unit"), str)):
            errors.append(f"{path}: dimension 值必须是 {{value:number, unit:string}}")
    elif token_type == "duration":
        if not isinstance(value, dict) or not isinstance(value.get("value"), (int, float)) \
                or value.get("unit") not in ("ms", "s"):
            errors.append(f"{path}: duration 值必须是 {{value:number, unit:ms|s}}")
    elif token_type == "cubicBezier":
        if (not isinstance(value, list) or len(value) != 4
                or any(not isinstance(v, (int, float)) for v in value)):
            errors.append(f"{path}: cubicBezier 必须是 4 个数字")
    return value


def load_tokens(path: Path) -> dict:
    """解析并校验；有错误抛 TokenError。返回扁平 token + palette/windows/weights。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise TokenError(f"{path}: 不是合法 JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise TokenError(f"{path}: 根必须是 JSON 对象")
    tokens: dict[str, dict] = {}
    errors: list[str] = []
    warnings: list[str] = []
    _walk(raw, [], None, tokens, errors, warnings)
    cache: dict[str, object] = {}
    for token_path, token in tokens.items():
        token["value"] = _resolve(token["raw"], token_path, tokens, [token_path], errors, cache)
    for token_path, token in tokens.items():
        token.pop("raw", None)
        token["value"] = _validate_value(token_path, token, errors, warnings)
    # 别名解析后类型可能继承自目标：按 value 形态兜底（只对未声明类型的 alias 生效）
    for token_path, token in tokens.items():
        if token["type"] == "unknown" and isinstance(token["value"], dict) \
                and "components" in token["value"]:
            token["type"] = "color"
            token["value"] = _validate_value(token_path, token, errors, warnings)
    if errors:
        raise TokenError("token 校验失败:\n  " + "\n  ".join(errors))
    palette = _collect_palette(tokens, warnings)
    windows, weights = _collect_lookdev(tokens, warnings)
    threshold = DEFAULT_PALETTE_THRESHOLD
    threshold_token = tokens.get("lookdev.paletteThreshold")
    if threshold_token is not None:
        if threshold_token["type"] != "number":
            warnings.append("lookdev.paletteThreshold 必须是 number（忽略）")
        else:
            threshold = float(threshold_token["value"])
    return {
        "version": TOKENS_VERSION,
        "file": str(path),
        "tokens": tokens,
        "palette": palette,
        "windows": windows,
        "weights": weights,
        "palette_threshold": threshold,
        "warnings": warnings,
        "errors": [],
    }


def _collect_palette(tokens: dict[str, dict], warnings: list[str]) -> list[dict]:
    palette = []
    seen = set()
    for path, token in tokens.items():
        if not path.startswith("color.palette."):
            continue
        if token["type"] != "color":
            warnings.append(f"{path}: color.palette 下只接受 color token（跳过 {token['type']}）")
            continue
        if path in seen:
            continue
        if token.get("alias"):
            # 别名是同一个颜色的第二个名字，不重复计入色板（否则覆盖率权重翻倍）
            continue
        seen.add(path)
        palette.append({
            "path": path,
            "name": path[len("color.palette."):],
            "hex": token.get("hex"),
            "rgb": token.get("rgb"),
            "oklch": token.get("oklch"),
            "description": token.get("description"),
            "extensions": token.get("extensions") or {},
        })
    if not palette:
        warnings.append("没有 color.palette.* token：色板覆盖率/ΔE 指标将不可用")
    return palette


def _collect_lookdev(tokens: dict[str, dict], warnings: list[str]) -> tuple[dict, dict]:
    windows: dict[str, dict] = {}
    weights: dict[str, dict] = {}
    for path, token in tokens.items():
        if token["type"] != "number":
            continue
        parts = path.split(".")
        if len(parts) >= 5 and parts[0] == "lookdev" and parts[1] == "window":
            profile, group, metric = parts[2], parts[3], ".".join(parts[4:])
            if metric not in COMPOSITION_METRICS:
                warnings.append(f"{path}: 未识别的窗口指标 '{metric}'（保留但评分不消费）")
            windows.setdefault(profile, {}).setdefault(group, {})[metric] = float(token["value"])
        elif len(parts) >= 4 and parts[0] == "lookdev" and parts[1] == "weight":
            group = parts[2]
            metric = ".".join(parts[3:])
            weights.setdefault(group, {})[metric] = float(token["value"])
    if not windows:
        warnings.append("没有 lookdev.window.* 窗口：只能做观察不能做达标判读")
    return windows, weights


def window_for(tokens: dict, profile: str, group: str) -> dict[str, float]:
    """取某 profile/group 的窗口；group 缺省回退 profile.default。"""
    profile_windows = tokens["windows"].get(profile) or {}
    return dict(profile_windows.get("default") or {}, **profile_windows.get(group, {}))


def weight_for(tokens: dict, group: str, metric: str) -> float:
    group_weights = tokens["weights"].get(group) or {}
    return float(group_weights.get(metric, group_weights.get("default", 1.0)))


# ======================================================================================
# lint / CLI
# ======================================================================================

def lint(path: Path, strict: bool, require_palette: bool) -> dict:
    report: dict = {"file": str(path), "errors": [], "warnings": []}
    try:
        data = load_tokens(path)
    except TokenError as exc:
        report["errors"] = [str(exc)]
        return report
    report["warnings"] = data["warnings"]
    report["stats"] = {
        "tokens": len(data["tokens"]),
        "palette": len(data["palette"]),
        "profiles": sorted(data["windows"]),
        "groups": sorted({group for profile in data["windows"].values() for group in profile}),
    }
    if require_palette and not data["palette"]:
        report["errors"].append("要求 color.palette.* 非空（美术规范必须给出色板）")
    for path_key, token in data["tokens"].items():
        text = " ".join(str(value) for value in (
            token.get("description"), token.get("extensions")) if value)
        if "TODO" in text.upper():
            report["warnings"].append(f"{path_key}: 描述仍含 TODO（示例占位未替换）")
    if strict and report["warnings"]:
        report["errors"].extend(f"strict: {warning}" for warning in report["warnings"])
    report["ok"] = not report["errors"]
    return report


def _palette_report(data: dict) -> dict:
    return {"file": data["file"], "palette": data["palette"],
            "threshold_delta_e": data["palette_threshold"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DTCG design tokens 解析/校验/色板")
    parser.add_argument("command", nargs="?", choices=("lint", "resolve", "palette", "check-image"),
                        help="lint/resolve/palette/check-image")
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--image", type=Path, help="check-image 的 PNG")
    parser.add_argument("--roi", default="torso", help="check-image ROI（同 image_metrics）")
    parser.add_argument("--mask", choices=("nonsky", "subject", "all"), default="nonsky")
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--strict", action="store_true", help="warnings 视为错误")
    parser.add_argument("--require-palette", action="store_true",
                        help="要求 color.palette.* 非空（美术规范色板必须存在）")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.command or not args.file:
        parser.error("需要 command 与 file（或 --self-test）")

    if args.command == "lint":
        report = lint(args.file, args.strict, args.require_palette)
        text = json.dumps(report, indent=2, ensure_ascii=False)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if report["ok"] else 1

    data = load_tokens(args.file)
    if args.command == "resolve":
        output = {"file": data["file"], "tokens": data["tokens"],
                  "windows": data["windows"], "weights": data["weights"],
                  "palette_threshold": data["palette_threshold"]}
    elif args.command == "palette":
        output = _palette_report(data)
    else:
        if not args.image:
            parser.error("check-image 需要 --image")
        if not data["palette"]:
            raise SystemExit("token 文件没有 color.palette.*，无法做色板匹配")
        result = im.analyze_file(args.image, data["palette"], args.roi, args.mask, args.stride,
                                 data["palette_threshold"])
        output = {"file": str(args.image), "roi": result["roi"], "mask": args.mask,
                  "stride": args.stride, "palette": result["palette"],
                  "dominant_colors": result["dominant_colors"],
                  "luma": result["luma"], "color": result["color"],
                  "definitions": result["definitions"]}
    text = json.dumps(output, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"[design_tokens] 写入 {args.out}")
    else:
        print(text)
    return 0


def _self_test() -> int:
    import tempfile

    problems: list[str] = []
    case = {
        "$description": "self-test",
        "color": {
            "$type": "color",
            "palette": {
                "skin": {"$value": {"colorSpace": "srgb", "components": [0.78, 0.62, 0.48]},
                          "$description": "未替换 TODO 示例"},
                "alias": {"$value": "{color.palette.skin}"},
            },
        },
        "lookdev": {
            "window": {
                "day": {
                    "body": {"p98Min": {"$type": "number", "$value": 0.82},
                             "p98Max": {"$type": "number", "$value": 0.93}},
                },
            },
            "weight": {"body": {"p98": {"$type": "number", "$value": 2}}},
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "tokens.json"
        path.write_text(json.dumps(case), encoding="utf-8")
        data = load_tokens(path)
        if len(data["palette"]) != 1:
            problems.append(f"alias 不应重复计入 palette: {len(data['palette'])}")
        if data["palette"] and data["palette"][0]["hex"] != "#c79e7a":
            problems.append(f"color 归一化异常: {data['palette'][0]['hex']}")
        window = window_for(data, "day", "body")
        if window.get("p98Min") != 0.82 or window.get("p98Max") != 0.93:
            problems.append(f"窗口解析异常: {window}")
        if weight_for(data, "body", "p98") != 2.0:
            problems.append("权重解析异常")

        cycle = {"a": {"$type": "number", "$value": "{b}"},
                 "b": {"$type": "number", "$value": "{a}"}}
        path.write_text(json.dumps(cycle), encoding="utf-8")
        try:
            load_tokens(path)
            problems.append("循环别名未被拒绝")
        except TokenError:
            pass

        bad = {"x": {"$type": "color",
                     "$value": {"colorSpace": "srgb", "components": [1.2, 0, 0]}}}
        path.write_text(json.dumps(bad), encoding="utf-8")
        try:
            load_tokens(path)
            problems.append("越界 color 未被拒绝")
        except TokenError:
            pass

    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}", file=sys.stderr)
        return 1
    print("[OK] design_tokens self-test: 别名/色值/窗口/权重/循环/越界全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
