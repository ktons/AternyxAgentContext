"""rd_probe.py -- qrenderdoc --python 单次启动批量取证 (一次 replay 回答一轮全部问题).

动机: 每个深度分析调用都会新起一个 qrenderdoc 进程 (加载+replay 分钟级成本);
把一轮排查要问的问题全写进 spec, 一次跑完, 只付一次启动成本 (2026-10-22 拍板).

用法 (仓库根目录, qrenderdoc 内嵌 Python, 非系统 python):
  先用系统 python 写好 spec 文件 (JSON, 结构见 RD_PROBE_SPEC_FORMAT),
  然后:
  DCC_MCP_RENDERDOC_CAPTURE='D:\\...\\frame.rdc' \
  RD_PROBE_SPEC='D:\\...\\probe_spec.json' \
  DCC_MCP_RENDERDOC_RESOURCE_STATUS='D:\\...\\probe_result.json' \
  'C:/Program Files/RenderDoc/qrenderdoc.exe' --python rd_probe.py
  纹理 PNG 导出目录 = RD_OUT (缺省 temp/rd_probe)

RD_PROBE_SPEC_FORMAT:
  {"probes": [
    {"type": "actions",  "name_filter": "sub", "max": 500}                  # action 树
    {"type": "draws",    "eids": [65], "max_draws": 200}                    # 每 draw 绑定+全屏 vp/sc
    {"type": "state",    "eids": [65]}                                      # 任意 eid 管线状态快照
    {"type": "textures", "res": [123], "all_over_px": 0, "event": null}     # 导 PNG (res 列表或全量)
    {"type": "sample",   "event": 65, "points": [[res, x, y], ...]}         # 纹理原始读数
    {"type": "buffer",   "items": [[res, bytes], ...], "as": "f32|u32"}     # buffer 原始字节
    {"type": "history",  "res": 123, "points": [[x, y], ...], "event": null,
                         "pick": true}                                      # 像素历史 (+最后通过者)
    {"type": "debug_pixel", "event": 65, "points": [[x, y], ...],
                         "vars_filter": ["shadow", "uv"], "max_steps": 200} # PS 单步 (变量名过滤)
    {"type": "postvs",   "eids": [65], "vertices": 8}                       # post-VS 顶点 clip 坐标
    {"type": "usage",    "res": [123]}                                      # 资源全生命周期
  ]}

probe 顺序执行; "event" 字段先 SetFrameEvent 再取数 (历史/单步类必填目标 draw 的 eid).
只支持 RenderDoc 1.46 (本 skill 维护口径); API 形态表见 references/qrenderdoc-python.md.
"""

import json
import os
import re
import struct
import traceback

MAX_STEPS_RECORDED = 400
MAX_BUFFER_BYTES = 65536


def _safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_.")
    return value[:60] or "tex"


def _fmt_stride(fmt_name):
    f = fmt_name.upper()
    if f.startswith(("D32", "R32")):
        return "f32", 4
    if f.startswith("R16G16B16A16") or f.startswith("R16"):
        return "f16", 8
    if f.startswith(("R8G8B8A8", "B8G8R8A8")):
        return "u8", 4
    if f.startswith("R11G11B10") or f.startswith("R32G32"):
        return "f32", 12 if f.startswith("R11") else 8
    return "f32", 16


def _decode(kind, data, off):
    if kind == "f32":
        return struct.unpack_from("<f", data, off)
    if kind == "f16":
        return struct.unpack_from("<4e", data, off)
    if kind == "u8":
        return struct.unpack_from("<4B", data, off)
    return ()


def _seq(x, n=4):
    # rd 的 SWIG 定长数组包装: list() 与索引都做兜底
    try:
        return list(x)[:n]
    except Exception:
        out = []
        for i in range(n):
            try:
                out.append(x[i])
            except Exception:
                break
        return out


def _var_value(v):
    val = getattr(v, "value", None)
    for attr in ("f32v", "u32v"):
        arr = getattr(val, attr, None)
        if arr is not None:
            try:
                return [round(float(a), 5) for a in _seq(arr)]
            except Exception:
                return ["?"]
    return ["?"]


def _hist_flags(h):
    flags = []
    for attr in ("backfaceCulled", "depthTestFailed", "shaderDiscarded", "scissorClipped",
                 "viewClipped", "sampleMasked", "unboundPS", "depthClipped",
                 "depthBoundsFailed", "stencilTestFailed", "predicationSkipped",
                 "directShaderWrite"):
        try:
            if getattr(h, attr):
                flags.append(attr)
        except Exception:
            pass
    return flags


def _hist_record(h, with_values):
    # 本版本 PixelModification 的通过标志是方法 Passed(), 不是属性
    rec = {"eid": int(h.eventId), "flags": _hist_flags(h)}
    try:
        rec["passed"] = bool(h.Passed())
    except Exception:
        try:
            rec["passed"] = bool(h.passed)
        except Exception:
            pass
    if not with_values:
        return rec
    for key, obj in (("postMod", getattr(h, "postMod", None)),
                     ("shaderOut", getattr(h, "shaderOut", None))):
        # postMod/shaderOut 是 ShaderVariable: 数值在 .col.floatValue
        col = getattr(obj, "col", None) if obj is not None else None
        if col is not None:
            try:
                rec[key] = [round(float(a), 5) for a in _seq(getattr(col, "floatValue", col))]
            except Exception:
                pass
    return rec


def main():
    out = {"error": None, "capture": os.environ.get("DCC_MCP_RENDERDOC_CAPTURE", ""),
           "probes": []}
    try:
        import renderdoc as rd

        with open(os.environ["RD_PROBE_SPEC"], "r", encoding="utf-8") as f:
            spec = json.load(f)
        probes = spec.get("probes", [])
        out_dir = os.environ.get("RD_OUT") or os.path.join("temp", "rd_probe")
        os.makedirs(out_dir, exist_ok=True)

        context = globals().get("pyrenderdoc")
        context.LoadCapture(os.environ["DCC_MCP_RENDERDOC_CAPTURE"],
                            rd.ReplayOptions(),
                            os.environ["DCC_MCP_RENDERDOC_CAPTURE"], False, True)

        def cb(controller):
            names = {int(r.resourceId): str(r.name) for r in controller.GetResources()}
            # GetTextures 回调开头先快照 (防御写法; 1.46 实测 SetFrameEvent 后仍返回全量)
            tex_snapshot = list(controller.GetTextures())
            texinfo = {int(t.resourceId): (int(t.width), int(t.height), str(t.format.Name()))
                       for t in tex_snapshot}
            resmap = {int(r.resourceId): r.resourceId for r in controller.GetResources()}

            def walk(actions):
                for a in actions:
                    yield a
                    yield from walk(a.children)

            def set_event(eid):
                if eid:
                    controller.SetFrameEvent(int(eid), True)

            # LoadCapture 后 replay 停在帧首; 不指定 event 的取数一律落到帧末 (最后一个 draw 之后)
            last_draw_eid = 0
            for a in walk(controller.GetRootActions()):
                if a.flags & rd.ActionFlags.Drawcall:
                    last_draw_eid = max(last_draw_eid, int(a.eventId))

            def read_texture_points(res, pts, eid):
                set_event(eid)
                width, _, fmt = texinfo.get(res, (0, 0, "?"))
                kind, stride = _fmt_stride(fmt)
                data = controller.GetTextureData(resmap[res], rd.Subresource(0, 0, 0))
                rows = []
                for x, y in pts:
                    off = (y * width + x) * stride
                    if data and off + stride <= len(data):
                        rows.append({"xy": [x, y],
                                     "value": ["%.5f" % v for v in _decode(kind, data, off)]})
                    else:
                        rows.append({"xy": [x, y], "value": None})
                return {"res": res, "fmt": fmt, "w": width, "rows": rows}

            def pixel_history(res, pts, eid, pick):
                set_event(eid)
                rows = []
                for x, y in pts:
                    hist = controller.PixelHistory(resmap[res], x, y,
                                                   rd.Subresource(0, 0, 0),
                                                   rd.CompType.Typeless)
                    recs = [_hist_record(h, True) for h in hist]
                    row = {"xy": [x, y], "count": len(recs), "records": recs}
                    if pick:
                        passed = [r for r in recs if r.get("passed")]
                        row["pick"] = passed[-1] if passed else None
                    rows.append(row)
                return {"res": res, "pixels": rows}

            def debug_pixel_probe(eid, pts, keys, max_steps):
                set_event(eid)
                rows = []
                traces = []
                for x, y in pts:
                    row = {"xy": [x, y]}
                    try:
                        tr = controller.DebugPixel(x, y, rd.DebugPixelInputs())
                        traces.append(tr)
                        # 本版本 trace 无预置 steps: 需经 ContinueDebug 逐批驱动,
                        # debugger 缺失 (如 event 未覆盖该像素) 则无法单步
                        debugger = getattr(tr, "debugger", None)
                        if debugger is None:
                            raise RuntimeError(
                                "no debugger for this invocation "
                                "(event 未覆盖该像素或 shader 不可调试)")
                        consts = []
                        # 1.46 实测 constantBlocks 是扁平 ShaderVariable 列表;
                        # 元素带 .name 即按单变量处理 (防御嵌套结构)
                        for blk in getattr(tr, "constantBlocks", None) or []:
                            variables = [blk] if hasattr(blk, "name") else list(blk)
                            for v in variables:
                                consts.append({"name": str(v.name),
                                               "value": _var_value(v)})
                        row["constants"] = consts
                        matched = []
                        total = 0
                        while total < MAX_STEPS_RECORDED:
                            batch = list(controller.ContinueDebug(debugger) or ())
                            if not batch:
                                break
                            for st in batch:
                                total += 1
                                if total > max_steps:
                                    continue
                                # 本版本 ShaderDebugState 无 vars: 每步变量变化在
                                # changes, 每项是 .before/.after 两个 ShaderVariable
                                svars = []
                                for ch in getattr(st, "changes", None) or []:
                                    after = getattr(ch, "after", None)
                                    before = getattr(ch, "before", None)
                                    var = after if getattr(after, "name", "") else before
                                    name = str(getattr(var, "name", ""))
                                    if not name or (keys and not any(
                                            k.lower() in name.lower() for k in keys)):
                                        continue
                                    svars.append({"name": name,
                                                  "value": _var_value(var)})
                                if svars:
                                    matched.append({"step": total, "vars": svars})
                        row["steps_total"] = total
                        row["steps_matched"] = matched
                        row["steps_matched_count"] = len(matched)
                        row["truncated"] = total > max_steps
                    except Exception as exc:
                        row["error"] = repr(exc)
                    rows.append(row)
                for tr in traces:
                    try:
                        controller.FreeTrace(tr)
                    except Exception:
                        pass
                return {"pixels": rows}

            def draw_snapshot(controller_, a, pipe):
                outs = []
                for slot, o in enumerate(pipe.GetOutputTargets()):
                    if o.resource == rd.ResourceId.Null():
                        continue
                    rid = int(o.resource)
                    w, h, fmt = texinfo.get(rid, (0, 0, ""))
                    outs.append({"slot": slot, "res": rid,
                                 "name": names.get(rid, ""), "wh": [w, h], "fmt": fmt})
                ro = []
                seen = set()
                for stage, tag in ((rd.ShaderStage.Pixel, "ps"), (rd.ShaderStage.Vertex, "vs")):
                    for used in pipe.GetReadOnlyResources(stage, True):
                        if used.descriptor.resource == rd.ResourceId.Null():
                            continue
                        rid = int(used.descriptor.resource)
                        if rid not in seen:
                            seen.add(rid)
                            ro.append({"stage": tag, "res": rid, "name": names.get(rid, "")})
                row = {"eid": int(a.eventId),
                       "name": str(a.GetName(controller_.GetStructuredFile())),
                       "numIndices": int(a.numIndices), "outputs": outs, "readOnly": ro}
                if int(a.numIndices) == 3:
                    try:
                        vp = pipe.GetViewport(0)
                        row["viewport"] = {k: float(getattr(vp, k))
                                           for k in ("x", "y", "width", "height")}
                        sc = pipe.GetScissor(0)
                        row["scissor"] = {k: int(getattr(sc, k))
                                          for k in ("x", "y", "width", "height")}
                    except Exception as e:
                        row["viewport_err"] = repr(e)
                return row

            for p in probes:
                kind = p.get("type")
                try:
                    if kind == "actions":
                        rows = []
                        for a in walk(controller.GetRootActions()):
                            if p.get("name_filter") and p["name_filter"] not in str(
                                    a.GetName(controller.GetStructuredFile())):
                                continue
                            rows.append({"eid": int(a.eventId), "flags": str(a.flags),
                                         "name": str(a.GetName(controller.GetStructuredFile())),
                                         "numIndices": int(a.numIndices)})
                            if len(rows) >= int(p.get("max", 500)):
                                break
                        out["probes"].append({"type": kind, "actions": rows})

                    elif kind == "draws":
                        want = set(int(e) for e in p.get("eids", []))
                        rows = []
                        for a in walk(controller.GetRootActions()):
                            if not (a.flags & rd.ActionFlags.Drawcall):
                                continue
                            if want and int(a.eventId) not in want:
                                continue
                            set_event(int(a.eventId))
                            rows.append(draw_snapshot(controller, a,
                                                      controller.GetPipelineState()))
                            if len(rows) >= int(p.get("max_draws", 200)):
                                break
                        out["probes"].append({"type": kind, "draws": rows})

                    elif kind == "state":
                        rows = []
                        for eid in p.get("eids", []):
                            set_event(int(eid))
                            a = None
                            for act in walk(controller.GetRootActions()):
                                if int(act.eventId) == int(eid):
                                    a = act
                                    break
                            rows.append(draw_snapshot(controller, a,
                                                      controller.GetPipelineState())
                                        if a is not None else {"eid": eid, "missing": True})
                        out["probes"].append({"type": kind, "states": rows})

                    elif kind == "textures":
                        set_event(p.get("event") or last_draw_eid)
                        if p.get("res"):
                            wanted = [int(r) for r in p["res"]]
                        else:
                            floor = int(p.get("all_over_px", 0))
                            wanted = [rid for rid, (w, h, _) in texinfo.items()
                                      if rid and w > 4 and h > 4 and min(w, h) >= floor]
                        saved = []
                        for rid in wanted:
                            if rid not in texinfo:
                                saved.append({"res": rid, "missing": True})
                                continue
                            w, h, fmt = texinfo[rid]
                            save = rd.TextureSave()
                            save.resourceId = resmap[rid]
                            save.destType = rd.FileType.PNG
                            save.alpha = rd.AlphaMapping.Preserve
                            save.mip = 0
                            save.slice.sliceIndex = 0
                            fn = os.path.join(out_dir, "tex_%d_%dx%d_%s.png" % (
                                rid, w, h, _safe_name(names.get(rid, ""))))
                            ok = bool(controller.SaveTexture(save, fn))
                            saved.append({"res": rid, "name": names.get(rid, ""),
                                          "wh": [w, h], "fmt": fmt,
                                          "file": fn if ok else None})
                        out["probes"].append({"type": kind, "textures": saved})

                    elif kind == "sample":
                        groups = {}
                        for item in p.get("points", []):
                            res, x, y = int(item[0]), int(item[1]), int(item[2])
                            groups.setdefault(res, []).append((x, y))
                        rows = [read_texture_points(res, pts, p.get("event"))
                                for res, pts in groups.items()]
                        out["probes"].append({"type": kind, "samples": rows})

                    elif kind == "buffer":
                        rows = []
                        for rid, nbytes in p.get("items", []):
                            data = controller.GetBufferData(resmap[int(rid)], 0,
                                                            min(int(nbytes), MAX_BUFFER_BYTES))
                            if not data:
                                rows.append({"res": rid, "empty": True})
                                continue
                            mode = p.get("as", "f32")
                            if mode == "u32":
                                n = len(data) // 4
                                vals = struct.unpack_from("<%dI" % n, data, 0)
                                rows.append({"res": rid, "bytes": len(data),
                                             "u32": ["0x%08x" % v for v in vals[:256]]})
                            else:
                                n = len(data) // 4
                                vals = struct.unpack_from("<%df" % n, data, 0)
                                rows.append({
                                    "res": rid, "bytes": len(data),
                                    "f32": [["%.5f" % v for v in vals[g:g + 4]]
                                            for g in range(0, min(n, 256), 4)]})
                        out["probes"].append({"type": kind, "buffers": rows})

                    elif kind == "history":
                        out["probes"].append({"type": kind, **pixel_history(
                            int(p["res"]), [tuple(xy) for xy in p["points"]],
                            p.get("event"), bool(p.get("pick")))})

                    elif kind == "debug_pixel":
                        out["probes"].append({"type": kind, **debug_pixel_probe(
                            p.get("event"), [tuple(xy) for xy in p["points"]],
                            p.get("vars_filter", []),
                            int(p.get("max_steps", 200)))})

                    elif kind == "postvs":
                        rows = []
                        for eid in p.get("eids", []):
                            set_event(int(eid))
                            try:
                                info = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
                                nv = int(p.get("vertices", 8))
                                vdata = controller.GetBufferData(
                                    info.vertexResourceId, info.vertexByteOffset,
                                    nv * info.vertexByteStride)
                                verts = []
                                for i in range(nv):
                                    off = i * info.vertexByteStride
                                    if vdata and off + 16 <= len(vdata):
                                        verts.append(["%.4f" % v for v in
                                                      struct.unpack_from("<4f", vdata, off)])
                                rows.append({"eid": eid, "stride": int(info.vertexByteStride),
                                             "vertices": verts})
                            except Exception as e:
                                rows.append({"eid": eid, "error": repr(e)})
                        out["probes"].append({"type": kind, "postvs": rows})

                    elif kind == "usage":
                        rows = []
                        for rid in p.get("res", []):
                            usages = controller.GetUsage(resmap[int(rid)])
                            rows.append({"res": rid, "name": names.get(int(rid), ""),
                                         "usage": [{"eid": int(u.eventId), "use": str(u.usage)}
                                                   for u in usages]})
                        out["probes"].append({"type": kind, "usage": rows})

                    else:
                        out["probes"].append({"type": kind, "error": "unknown probe type"})

                except Exception as exc:
                    out["probes"].append({"type": kind, "error": "%r" % (exc,)})

        context.Replay().BlockInvoke(cb)
        context.CloseCapture()
    except BaseException as exc:
        out["error"] = "%r\n%s" % (exc, traceback.format_exc())
    finally:
        status_path = (os.environ.get("DCC_MCP_RENDERDOC_RESOURCE_STATUS")
                       or os.path.join("temp", "rd_probe", "probe_result.json"))
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1, ensure_ascii=False)


main()
raise SystemExit(0)
