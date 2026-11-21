"""rd_frame_report.py -- qrenderdoc --python 一次性帧报告生成器.

用法 (通过 qrenderdoc 内嵌 Python 运行, 不是系统 python):
  DCC_MCP_RENDERDOC_CAPTURE='<path>/frame.rdc' \
  RD_OUT='<workspace>/temp/rd_report' \
  DCC_MCP_RENDERDOC_RESOURCE_STATUS='<path>/report_status.json' \
  '<renderdoc>/qrenderdoc.exe' --python rd_frame_report.py

产出 (写入 RD_OUT):
  status.json          action 树 + 每个 drawcall 的 RT/PS只读资源/viewport + 纹理清单
  tex_<res>_<w>x<h>.png  帧结束时所有 >4px 纹理的内容 (颜色/深度/交换链)
"""

import json
import os
import re
import traceback


def _safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_.")
    return value[:60] or "tex"


def main():
    out = {"error": None, "actions": [], "draws": [], "textures": []}
    try:
        import renderdoc as rd

        capture_file = os.environ["DCC_MCP_RENDERDOC_CAPTURE"]
        output_dir = os.environ["RD_OUT"]
        os.makedirs(output_dir, exist_ok=True)
        context = globals().get("pyrenderdoc")
        if context is None:
            raise RuntimeError("qrenderdoc capture context unavailable")

        context.LoadCapture(capture_file, rd.ReplayOptions(), capture_file, False, True)

        def cb(controller):
            names = {int(r.resourceId): str(r.name) for r in controller.GetResources()}
            # GetTextures 回调开头先快照 (防御写法; 1.46 实测 SetFrameEvent 后仍返回全量)
            tex_snapshot = list(controller.GetTextures())

            def walk(actions):
                for a in actions:
                    yield a
                    yield from walk(a.children)

            for a in walk(controller.GetRootActions()):
                row = {
                    "eid": int(a.eventId),
                    "flags": str(a.flags),
                    "name": str(a.GetName(controller.GetStructuredFile())),
                    "numIndices": int(a.numIndices),
                }
                out["actions"].append(row)
                if not (a.flags & rd.ActionFlags.Drawcall):
                    continue
                controller.SetFrameEvent(int(a.eventId), True)
                pipe = controller.GetPipelineState()
                # 注意: 本版本 RenderDoc 输出目标/只读资源都是 Descriptor, 字段是 .resource 不是 .resourceId
                outs = []
                for slot, o in enumerate(pipe.GetOutputTargets()):
                    if o.resource == rd.ResourceId.Null():
                        continue
                    outs.append({"slot": slot, "res": int(o.resource), "name": names.get(int(o.resource), "")})
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
                draw = {"eid": int(a.eventId), "numIndices": int(a.numIndices),
                        "outputs": outs, "readOnly": ro}
                # 全屏 draw 额外记录 viewport/scissor (偏移/尺寸错误是常见根因)
                if int(a.numIndices) == 3:
                    try:
                        vp = pipe.GetViewport(0)
                        draw["viewport"] = {k: float(getattr(vp, k)) for k in ("x", "y", "width", "height")}
                        sc = pipe.GetScissor(0)
                        draw["scissor"] = {k: int(getattr(sc, k)) for k in ("x", "y", "width", "height")}
                    except Exception as e:
                        draw["viewport_err"] = repr(e)
                out["draws"].append(draw)

            # 走到最后一个事件, 保证纹理处于帧末状态
            draws = [d for d in out["draws"]]
            if draws:
                controller.SetFrameEvent(draws[-1]["eid"], True)

            for tex in tex_snapshot:
                rid = int(tex.resourceId)
                if rid == 0 or int(tex.width) < 4:
                    continue
                save = rd.TextureSave()
                save.resourceId = tex.resourceId
                save.destType = rd.FileType.PNG
                save.alpha = rd.AlphaMapping.Preserve
                save.mip = 0
                save.slice.sliceIndex = 0
                fn = os.path.join(output_dir, "tex_%d_%dx%d_%s.png" % (
                    rid, int(tex.width), int(tex.height), _safe_name(names.get(rid, ""))))
                ok = bool(controller.SaveTexture(save, fn))
                out["textures"].append({"res": rid, "name": names.get(rid, ""),
                                        "w": int(tex.width), "h": int(tex.height),
                                        "fmt": str(tex.format.Name()), "file": fn if ok else None})

        context.Replay().BlockInvoke(cb)
        context.CloseCapture()
    except BaseException as exc:
        out["error"] = "%r\n%s" % (exc, traceback.format_exc())
    finally:
        status_path = os.environ.get("DCC_MCP_RENDERDOC_RESOURCE_STATUS") or os.path.join(
            os.environ.get("RD_OUT", "."), "status.json")
        with open(status_path, "w") as f:
            json.dump(out, f, indent=1)


main()
raise SystemExit(0)
