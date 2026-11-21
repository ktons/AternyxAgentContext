#!/usr/bin/env python3
"""lookdev 试跑适配器：应用参数 → 固定 leg 出图 → 指标 → 还原 → 评分。

定位：`parameters_search.py` 的评估器参考实现（对应 references/08-search.md）。一次
trial 起一个离屏编辑器会话，按 `--params` 应用观察参数（Volume 字段 / 灯光朝向 /
材质数值），逐 leg `preset.apply` + `editor.capture`，复用
`ag_lookdev_shading_eval.py` 的解码/ROI 口径与 `image_metrics.py` 的色彩/构图口径，
最后恢复现场并写 `trial_metrics.json`（+ `trial_score.json`）。

参数 JSON（`--params` 内联或 `--params-file`）：
  {
    "volume": {"exposure": 1.15, "ambientIntensity": 0.30, "skyTop": [0.02,0.03,0.06]},
    "lights": {"KeyLight": {"azimuthDeg": 35, "elevationDeg": 40}},
    "materials": [
      {"path": "Assets/material/char/char_skin.mat", "property": "gSkinSSSParams",
       "numbers": [1.0, 2.0, 0.1, 0.0]}
    ]
  }

  - `volume` 键必须是 EnvironmentVolumeComponent 现有字段；未知键直接拒绝（防拼错静默无效）。
  - `lights` 键是场景实体名；`azimuthDeg/elevationDeg` 描述灯光**传播方向**（+Z 朝向），
    由脚本换算四元数；也可直接给 `rotation:[x,y,z,w]` 或 `position:[x,y,z]`。
  - `materials` 按整组件 `material.set` 落盘（走 Codec 出口），结束后还原并读回校验。

纪律：
  - 每次 trial 只改 `--params` 列出的字段；未列出的观察态保持场景原值，结束逐项还原。
  - 逐 leg 应用灯光覆盖发生在 `preset.apply` seek 之后；采后复读灯光 Transform，若被动画
    覆写则本 trial 记失败（否则搜索到的「灯位」并非画面实际灯位）。
  - 采后核 `git status` 新增项（编辑器可能回写 `.mat.meta`），非预期改动写进报告告警。
  - 不写场景/资产（material.set 属参数级落盘，还原时写回原值）；用完杀编辑器。

用法：
  python .../lookdev_trial.py --params-file temp/p.json --out temp/trial0 \
      --legs front_front,left45_left45,back180_back,face_front,face_left45 \
      --tokens .agents/skills/graphics-iteration/templates/lookdev.tokens.json --profile day
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ag_lookdev_shading_eval as master  # noqa: E402
import image_metrics  # noqa: E402

TRANSFORM_TYPE = "TransformComponent"
VOLUME_TYPE = "EnvironmentVolumeComponent"
TRIAL_VERSION = 1
DEFAULT_LEGS = ("front_front", "left45_left45", "back180_back",
                "face_front", "face_left45", "hair_front", "weapon_front")


def _close(a, b, tol: float = 1e-6) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    return a == b


def quat_from_forward(forward: list[float] | tuple[float, float, float]) -> list[float]:
    """把 +Z 转到 forward 的四元数（与 Transform 约定一致：局部 +Z = 前向）。"""
    fx, fy, fz = (float(v) for v in forward)
    length = math.sqrt(fx * fx + fy * fy + fz * fz)
    if length <= 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    fx, fy, fz = fx / length, fy / length, fz / length
    dot = fz  # (0,0,1)·f
    if dot > 1.0 - 1e-9:
        return [0.0, 0.0, 0.0, 1.0]
    if dot < -1.0 + 1e-9:
        return [0.0, 1.0, 0.0, 0.0]  # 绕 +Y 180°
    axis = (-fy, fx, 0.0)  # (0,0,1) × f
    angle = math.acos(max(-1.0, min(1.0, dot)))
    s = math.sin(angle / 2.0)
    return [axis[0] / math.hypot(axis[0], axis[1]) * s,
            axis[1] / math.hypot(axis[0], axis[1]) * s, 0.0, math.cos(angle / 2.0)]


def forward_from_azimuth(azimuth_deg: float, elevation_deg: float) -> list[float]:
    """传播方向：az 从 +Z 向 +X，el 从水平面向 +Y（度）。"""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    return [math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)]


def read_light_transform(client, entity: dict) -> dict:
    return client.components(entity["index"], entity["generation"])[TRANSFORM_TYPE]


def find_entities(client) -> dict[str, dict]:
    found: dict[str, dict] = {}
    duplicates: set[str] = set()
    for entity in client.tree()["entities"]:
        name = entity.get("name")
        if name in found:
            duplicates.add(name)
        found[name] = entity["id"]
    for name in duplicates:
        found.pop(name, None)
    return found


def apply_volume(client, volume_entity: dict, before: dict, fields: dict) -> dict:
    after = copy.deepcopy(before)
    unknown = sorted(set(fields) - set(before))
    if unknown:
        raise SystemExit(f"volume 字段不存在: {unknown}（现有字段: {sorted(before)}）")
    after.update(fields)
    client.set_component(volume_entity["index"], volume_entity["generation"], VOLUME_TYPE, after)
    readback = master.read_volume(client, volume_entity)
    mismatched = [key for key, value in fields.items() if not _close(readback.get(key), value)]
    if mismatched:
        raise SystemExit(f"volume 写回不符: {mismatched}")
    return readback


def snapshot_material_files(project_root: Path, path: str) -> list[dict]:
    """记录 .mat 与 .mat.meta 原始字节：material.set 会升级格式，恢复必须回到字节级。"""
    files = []
    for suffix in ("", ".meta"):
        file_path = project_root / f"{path}{suffix}"
        if file_path.is_file():
            data = file_path.read_bytes()
            files.append({"file": str(file_path), "bytes": data,
                          "sha256": hashlib.sha256(data).hexdigest()})
    return files


def restore_material_files(originals: list[dict]) -> bool:
    ok = True
    for item in originals:
        for snapshot in item.get("files", []):
            file_path = Path(snapshot["file"])
            file_path.write_bytes(snapshot["bytes"])
            current = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if current != snapshot["sha256"]:
                ok = False
                print(f"[lookdev_trial] material 字节还原不符: {file_path}", file=sys.stderr)
    return ok


def apply_materials(client, patches: list[dict]) -> list[dict]:
    originals = []
    seen_paths: dict[str, dict] = {}
    for patch in patches:
        path = patch.get("path")
        property_name = patch.get("property")
        numbers = patch.get("numbers")
        slots = patch.get("set")
        if not path or not property_name or (numbers is None and slots is None):
            raise SystemExit(f"material patch 需要 path/property 与 numbers 或 set: {patch}")
        result = client.request("material.get", {"path": path})["result"]
        material = result["material"]
        keywords = result.get("keywords") or {}
        if "keywords" in material or "enabled" in keywords:
            material["keywords"] = keywords.get("enabled", True)
        if path in seen_paths:
            item = seen_paths[path]
        else:
            item = {"path": path, "material": copy.deepcopy(material),
                    "files": snapshot_material_files(project_root, path)}
            seen_paths[path] = item
            originals.append(item)
        target = None
        for entry in material.get("properties", []):
            if entry.get("name") == property_name:
                target = entry
                break
        if target is None:
            raise SystemExit(f"{path}: 找不到属性 {property_name}")
        current = list(target.get("numbers", []))
        if numbers is not None:
            if len(current) != len(numbers):
                raise SystemExit(f"{path}:{property_name} numbers 长度 {len(numbers)} != {len(current)}")
            current = list(numbers)
        else:
            for index, value in slots.items():
                index = int(index)
                if not 0 <= index < len(current):
                    raise SystemExit(f"{path}:{property_name} set 下标 {index} 越界（长度 {len(current)}）")
                current[index] = value
        target["numbers"] = current
        client.request("material.set", {"path": path, "material": material})
        readback = client.request("material.get", {"path": path})["result"]["material"]
        observed = next((entry for entry in readback.get("properties", [])
                         if entry.get("name") == property_name), None)
        if observed is None or not _close(observed.get("numbers"), current):
            raise SystemExit(f"{path}:{property_name} 写回不符: {observed}")
    return originals


def light_override(transform: dict, override: dict) -> dict:
    updated = copy.deepcopy(transform)
    if "rotation" in override:
        updated["rotation"] = [float(value) for value in override["rotation"]]
    elif "azimuthDeg" in override or "elevationDeg" in override:
        forward = forward_from_azimuth(float(override.get("azimuthDeg", 0.0)),
                                       float(override.get("elevationDeg", 0.0)))
        updated["rotation"] = quat_from_forward(forward)
    if "position" in override:
        updated["position"] = [float(value) for value in override["position"]]
    return updated


def apply_lights(client, entities: dict[str, dict], overrides: dict) -> None:
    """逐灯覆盖 Transform（在当前值上改指定分量），并读回校验。"""
    for name, override in overrides.items():
        if name not in entities:
            raise SystemExit(f"灯实体不存在或重名: {name}（可用: {sorted(entities)}）")
        entity = entities[name]
        before = read_light_transform(client, entity)
        updated = light_override(before, override)
        client.set_component(entity["index"], entity["generation"], TRANSFORM_TYPE, updated)
        readback = read_light_transform(client, entity)
        if not _close(readback.get("rotation"), updated["rotation"], 1e-4) or \
                not _close(readback.get("position"), updated["position"], 1e-4):
            raise SystemExit(f"{name}: Transform 写回不符")


def snapshot_lights(client, entities: dict[str, dict], names: list[str]) -> dict[str, dict]:
    originals = {}
    for name in names:
        if name not in entities:
            raise SystemExit(f"灯实体不存在或重名: {name}（可用: {sorted(entities)}）")
        originals[name] = copy.deepcopy(read_light_transform(client, entities[name]))
    return originals


def restore_lights(client, entities: dict[str, dict], originals: dict[str, dict]) -> bool:
    ok = True
    for name, transform in originals.items():
        entity = entities[name]
        client.set_component(entity["index"], entity["generation"], TRANSFORM_TYPE, transform)
        readback = read_light_transform(client, entity)
        if not _close(readback.get("rotation"), transform["rotation"], 1e-4) or \
                not _close(readback.get("position"), transform["position"], 1e-4):
            ok = False
    return ok


def measure_leg(path: Path, leg: str, group: str, width: int, height: int,
                palette: list[dict] | None) -> dict:
    data = path.read_bytes()
    image_w, image_h, channels, rows = master.decode_png_bytes(data)
    if (image_w, image_h) != (width, height):
        raise SystemExit(f"{path}: 尺寸 {image_w}x{image_h} != 期望 {width}x{height}")
    roi_kind = "face" if group == "face" else "torso"
    roi_ref = master.FACE_ROI_REF if roi_kind == "face" else master.TORSO_ROI_REF
    roi = master.scale_roi(roi_ref, image_w, image_h)
    entry = {
        "leg": leg,
        "group": group,
        "kind": "trial",
        "file": path.name,
        "width": image_w,
        "height": image_h,
        "sha256": hashlib.sha256(data).hexdigest(),
        "roi": roi_kind,
        "roi_rect": list(roi),
    }
    entry.update(master.measure_roi(rows, channels, roi))
    if roi_kind != "face":
        entry.pop("shadow_coverage", None)
    color = image_metrics.analyze_pixels(image_w, image_h, channels, rows, palette, roi,
                                         "nonsky", stride=2)
    entry.update({
        "color": color.get("color"),
        "palette": color.get("palette"),
        "dominant_colors": color.get("dominant_colors"),
    })
    composition = image_metrics.analyze_pixels(image_w, image_h, channels, rows, None, None,
                                               "nonsky", stride=4)
    entry.update({
        "subject_bbox": composition.get("subject_bbox"),
        "subject_composition": composition.get("subject_composition"),
        "saliency": composition.get("saliency"),
        "saliency_composition": composition.get("saliency_composition"),
    })
    return entry


def run(args) -> dict:
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(out_dir.glob("capture_*.png")) + list(out_dir.glob("trial_*.json")):
        stale.unlink()

    params = json.loads(args.params_file.read_text(encoding="utf-8-sig")) if args.params_file \
        else json.loads(args.params)
    unknown_top = sorted(set(params) - {"volume", "lights", "materials", "note"})
    if unknown_top:
        raise SystemExit(f"参数顶层键未知: {unknown_top}")
    legs = [name.strip() for name in args.legs.split(",") if name.strip()]
    if not legs:
        raise SystemExit("--legs 为空")

    logging_setup(out_dir)
    status_before = master.git_status_lines()
    started = time.time()
    report: dict = {
        "trial_version": TRIAL_VERSION,
        "params": params,
        "legs_requested": legs,
        "arguments": {"out": str(out_dir), "legs": legs, "post": args.post,
                      "width": args.width, "height": args.height},
        "scene": args.scene,
        "git_status_before": status_before,
    }
    process = None
    client = None
    log_file = None
    volume_before: dict = {}
    camera_before: dict = {}
    light_originals: dict = {}
    material_originals: list = []
    restore = {"volume": None, "camera": None, "lights": None, "materials": None}
    try:
        process, log_file, startup = master.launch_editor(out_dir / "editor.log",
                                                          args.editor_timeout)
        client = master.connect_client()
        client.scene_load(args.scene)
        time.sleep(1.5)
        rig, camera_entity, volume_entity = master.resolve_rig_children(client)
        listing = client.preset_list()
        preset_names = {preset["name"] for preset in listing.get("presets", [])}
        missing = [name for name in legs if name not in preset_names]
        if missing:
            raise SystemExit(f"preset 不存在: {missing}（可用 {sorted(preset_names)}）")
        entities = find_entities(client)
        report["entities"] = {"camera": camera_entity, "volume": volume_entity,
                              "lights": sorted(entities)}

        volume_before = master.read_volume(client, volume_entity)
        camera_before = master.read_transform(client, camera_entity)
        report["volume_before"] = volume_before
        report["camera_before"] = camera_before

        if params.get("volume"):
            report["volume_applied"] = apply_volume(client, volume_entity, volume_before,
                                                    params["volume"])
        else:
            report["volume_applied"] = None
        if args.post == "neutral":
            fields = master.neutral_fields("off")
            applied = dict(report.get("volume_applied") or volume_before)
            applied.update(fields)
            client.set_component(volume_entity["index"], volume_entity["generation"],
                                 VOLUME_TYPE, applied)
            report["neutral_override"] = {"fields": fields,
                                          "verified": _close(master.read_volume(
                                              client, volume_entity).get("bloomStrength"), 0.0)}
            if not report["neutral_override"]["verified"]:
                raise SystemExit("neutral override 写回不符")
        if params.get("materials") and not args.allow_material_write:
            raise SystemExit("参数含 materials：material.set 会落盘并升级 .mat 格式，"
                             "必须显式加 --allow-material-write（结束后按字节还原）")
        if params.get("materials"):
            material_originals = apply_materials(client, params["materials"])
        if params.get("lights"):
            light_originals = snapshot_lights(client, entities, list(params["lights"]))

        previous_position = None  # 首个 leg 不等待相机位移（preset.apply 已同步 seek）
        records = []
        for leg in legs:
            client.preset_apply(leg)
            moved, transform, waited = master.wait_camera_moved(client, camera_entity,
                                                                previous_position)
            previous_position = list(transform["position"])
            time.sleep(0.15)  # seek 写回后给一帧渲染落地的余量
            if params.get("volume"):
                # seek 后重新覆盖：clip 可能把 exposure/ambient 等观察参数字段写回键值
                current_volume = master.read_volume(client, volume_entity)
                apply_volume(client, volume_entity, current_volume, params["volume"])
            if args.post == "neutral":
                neutral_volume = master.read_volume(client, volume_entity)
                neutral_volume.update(master.neutral_fields("off"))
                client.set_component(volume_entity["index"], volume_entity["generation"],
                                     VOLUME_TYPE, neutral_volume)
            if params.get("lights"):
                # seek 后重新覆盖：clip 采样可能把灯光写回键值
                apply_lights(client, entities, params["lights"])
            shot = master.capture_single(client, out_dir, args.width, args.height)
            source = Path(shot["path"])
            if not source.is_file():
                raise SystemExit(f"capture 产物缺失: {source}")
            target = out_dir / f"capture_{leg}_{shot['seq']}.png"
            source.replace(target)
            light_stable = True
            if params.get("lights"):
                for name in params["lights"]:
                    readback = read_light_transform(client, entities[name])
                    expected = light_override(light_originals[name], params["lights"][name])
                    if not _close(readback.get("rotation"), expected["rotation"], 1e-4):
                        light_stable = False
            volume_stable = True
            if params.get("volume"):
                readback = master.read_volume(client, volume_entity)
                volume_stable = all(_close(readback.get(key), value)
                                    for key, value in params["volume"].items())
            records.append({"leg": leg, "seq": shot["seq"], "file": target.name,
                            "seek_camera_moved": moved, "seek_wait_seconds": round(waited, 3),
                            "light_stable_after_capture": light_stable,
                            "volume_stable_after_capture": volume_stable})
            print(f"[lookdev_trial] {leg}: seq={shot['seq']} moved={moved} "
                  f"light_stable={light_stable} volume_stable={volume_stable}", flush=True)

        if params.get("lights") and not all(r["light_stable_after_capture"] for r in records):
            raise SystemExit("灯光覆盖在 capture 前被动画写回（本 trial 不可信）")
        if params.get("volume") and not all(r["volume_stable_after_capture"] for r in records):
            raise SystemExit("Volume 参数在 capture 前被动画写回（本 trial 不可信）")

        # 还原观察态
        client.set_component(camera_entity["index"], camera_entity["generation"],
                             TRANSFORM_TYPE, camera_before)
        restore["camera"] = _close(master.read_transform(client, camera_entity), camera_before)
        client.set_component(volume_entity["index"], volume_entity["generation"],
                             VOLUME_TYPE, volume_before)
        restore["volume"] = _close(master.read_volume(client, volume_entity), volume_before)
        if light_originals:
            restore["lights"] = restore_lights(client, entities, light_originals)
        if material_originals:
            # .mat 由 material.set 升级过格式，内容恢复不保证字节一致：停编辑器后按快照还原
            restore["materials"] = "pending"
        if not all(value is not False for value in restore.values()):
            raise SystemExit(f"观察态还原失败: {restore}")
        if material_originals:
            if args.keep_editor:
                raise SystemExit("materials 参数与 --keep-editor 不兼容（字节还原需要停编辑器）")
            master._stop_editor(process)
            process = None
            restore["materials"] = restore_material_files(material_originals)
            if not restore["materials"]:
                raise SystemExit("material 字节还原失败，请 git checkout 对应 .mat/.meta")
        report["restore"] = restore

        # 指标
        palette = None
        if args.tokens:
            from design_tokens import load_tokens
            palette = load_tokens(args.tokens)["palette"]
        leg_entries = []
        for record in records:
            group = master.preset_group(record["leg"])
            leg_entries.append(measure_leg(out_dir / record["file"], record["leg"], group,
                                           args.width, args.height, palette))
        shas = [entry["sha256"] for entry in leg_entries]
        freeze = [record["leg"] for record, sha in zip(records, shas)
                  if shas.count(sha) > 1]
        report.update({
            "legs": leg_entries,
            "captures": records,
            "freeze_suspects": sorted(set(freeze)),
            "editor": {"exe": str(master.EDITOR_EXE)},
            "timings": {"startup_seconds": round(startup, 3),
                        "total_seconds": round(time.time() - started, 3)},
        })
        log_counts = master.count_log_lines(out_dir / "editor.log")
        report["editor"].update(log_counts)
        with (out_dir / "editor.log").open("rb") as handle:
            report["editor"]["log_bytes"] = handle.seek(0, 2)
        report["git_status_after"] = master.git_status_lines()
        report["git_status_unexpected"] = [line for line in report["git_status_after"]
                                           if line not in status_before]
        if freeze:
            report["hard_failures"] = [f"疑似冻帧（leg 间 sha 相同）: {sorted(set(freeze))}"]
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        if args.keep_editor:
            print(f"[lookdev_trial] --keep-editor: pid={process.pid if process else '?'}")
        else:
            master._stop_editor(process)
        if material_originals:
            # 异常路径兜底：字节还原（material.set 已把 .mat 升级到新格式）
            restore_material_files(material_originals)
        if log_file is not None:
            try:
                log_file.close()
            except OSError:
                pass

    if args.tokens:
        from design_tokens import load_tokens
        import score_trial
        tokens = load_tokens(args.tokens)
        score = score_trial.score(report, tokens, args.profile, required_legs=legs,
                                  quality=None)
        report["score"] = score
        score_path = args.score_out or (out_dir / "trial_score.json")
        score_path.write_text(json.dumps({
            "score": score["score"],
            "feasible": score["feasible"] and not report.get("hard_failures"),
            "hard_failures": score["hard_failures"] + report.get("hard_failures", []),
            "components": score["components"],
            "per_leg": score["per_leg"],
            "observed_only": score["observed_only"],
            "trial": str(out_dir),
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif args.score_out:
        raise SystemExit("--score-out 需要 --tokens")

    (out_dir / "trial_metrics.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def logging_setup(out_dir: Path) -> None:
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="[%(levelname)s] %(name)s: %(message)s")
    handler = logging.FileHandler(out_dir / "trial.log", encoding="utf-8", mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    master.LOG.addHandler(handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="lookdev 试跑：应用参数/出图/还原/评分")
    parser.add_argument("--params", help="参数 JSON 字符串")
    parser.add_argument("--params-file", type=Path, help="参数 JSON 文件")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--legs", default=",".join(DEFAULT_LEGS))
    parser.add_argument("--scene", default=master.SCENE)
    parser.add_argument("--post", choices=("scene", "neutral"), default="scene",
                        help="scene=场景原样（搜索 look 用）；neutral=关 bloom/outline（判 shading）")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--tokens", type=Path)
    parser.add_argument("--profile", default="day")
    parser.add_argument("--score-out", type=Path)
    parser.add_argument("--allow-material-write", action="store_true",
                        help="允许 materials 补丁（会落盘 .mat；结束后按字节快照还原）")
    parser.add_argument("--editor-timeout", type=float, default=120.0)
    parser.add_argument("--keep-editor", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.params and not args.params_file:
        raise SystemExit("需要 --params 或 --params-file")
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = run(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 搜索侧需要失败 statt 崩溃
        error = {"score": None, "feasible": False,
                 "error": f"{type(exc).__name__}: {exc}"}
        (out_dir / "trial_score.json").write_text(
            json.dumps(error, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[lookdev_trial] 失败: {error['error']}", file=sys.stderr)
        return 1
    if report.get("score"):
        print(json.dumps({"score": report["score"]["score"],
                          "feasible": report["score"]["feasible"],
                          "hard_failures": report["score"]["hard_failures"]},
                         indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
