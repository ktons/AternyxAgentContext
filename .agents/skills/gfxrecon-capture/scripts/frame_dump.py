#!/usr/bin/env python3
"""frame_dump.py -- 单帧批量 dump 封装: 从 jsonl 生成 JSON spec → 一次 replay dump 全部目标 pass → dr.json 摘要。

动机: dump-resources 的命令行多旗标/文本多行形态会去重只剩一条, 真正的批量形态是
JSON spec 文件里 Draw 挂列表 (单条 CB 内多 draw, 2026-10-23 编辑器 capture 实测);
本脚本把 "提 pass 结构 → 配对专属 submit → 写 spec → replay → 读 dr.json" 固化成一步。

用法 (仓库根目录):
  python3 frame_dump.py <capture.jsonl> --frame 5 --out temp/gfxr/spec_f5.json [--print-plan]
  python3 frame_dump.py <capture.jsonl> --frame 5 --run --capture <cap.gfxr> --out-dir temp/gfxr/dump_out \
      [--passes 0,1,3] [--all-passes] [--scale 1.0] [--immutable]

行为:
  - 帧号 N 与 extract_passes.py 同口径 (第 N 条 vkBeginCommandBuffer 起的命令缓冲)。
  - dump 点 = 每个所选 pass 的最后一个 draw (坑 #13); fullscreen-only pass 用其 vkCmdDraw。
  - submit 配对: 归属该 CB 的专属 submit。目标帧若与前面的帧合并在同一 submit 组内
    (capture 开头录制/提交解耦), dump 会在超大 MTLCommandBuffer 上执行, MoltenVK 实测
    GPU 超时 device lost —— 此时报警告并建议重抓更晚的帧窗口 (目标帧前留 >=4 帧 lead-in)。
  - --run 需要 replay 系工具的 SDK 环境 (capture_frames.sh 同款发现逻辑, 或 GFXRECON_SDK_ROOT)。
  - dump 输出目录自动预建 (坑 #1)。
  - 索引只对生成它的同一条 jsonl / 同一个 .gfxr 有效, 换 capture 必须重新生成。
"""
import argparse
import json
import os
import subprocess
import sys


def parse_jsonl(path):
    """流式解析 jsonl: 返回 (帧列表, submit 索引列表)。
    帧元素: {"bcb": int, "passes": [{"begin","end","draws","fullscreens"}]}"""
    frames = []
    submits = []
    cur = None
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            fn = rec.get("function", {}).get("name", "")
            idx = rec.get("index")
            if fn == "vkBeginCommandBuffer":
                cur = {"bcb": idx, "passes": []}
                frames.append(cur)
            elif fn == "vkCmdBeginRenderPass" and cur is not None:
                cur["passes"].append({"begin": idx, "end": None, "draws": [], "fullscreens": []})
            elif fn == "vkCmdEndRenderPass" and cur is not None and cur["passes"]:
                cur["passes"][-1]["end"] = idx
            elif fn == "vkCmdDrawIndexed" and cur is not None and cur["passes"]:
                cur["passes"][-1]["draws"].append(idx)
            elif fn == "vkCmdDraw" and cur is not None and cur["passes"]:
                cur["passes"][-1]["fullscreens"].append(idx)
            elif fn == "vkQueueSubmit":
                submits.append(idx)
    return frames, submits


def pick_submit(bcb, submits):
    """CB 的专属 submit = 大于 bcb 的最小 submit (该 CB 与其后至下一 submit 之间的 CB 合并提交)。"""
    greater = [s for s in submits if s > bcb]
    return min(greater) if greater else None


def build_spec(frame, passes, submits):
    """生成单 CB 批量 spec (Draw 列表与 RenderPass pair 按同序一一对应, 实测形态)。"""
    bcb = frame["bcb"]
    submit = pick_submit(bcb, submits)
    if submit is None:
        raise SystemExit(f"错误: CB {bcb} 之后没有 vkQueueSubmit (capture 被截断?), 无法生成 spec")
    draws = []
    pairs = []
    for p in passes:
        d = p["draws"][-1] if p["draws"] else (p["fullscreens"][0] if p["fullscreens"] else None)
        if d is None:
            continue
        draws.append(d)
        pairs.append([p["begin"], p["end"]])
    spec = {"BeginCommandBuffer": [bcb], "Draw": [draws],
            "RenderPass": [pairs], "QueueSubmit": [submit]}
    return spec, submit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--frame", type=int, default=1, help="第几条命令缓冲 (默认 1, 同 extract_passes)")
    ap.add_argument("--passes", default="", help="只取这些 pass 序号, 逗号分隔 (如 0,1,3)")
    ap.add_argument("--all-passes", action="store_true", help="含 fullscreen-only pass (post 链)")
    ap.add_argument("--out", default="", help="spec 输出路径 (缺省 <jsonl同目录>/frame_dump_spec_f<N>.json)")
    ap.add_argument("--run", action="store_true", help="生成 spec 后直接调 gfxrecon-replay 执行 dump")
    ap.add_argument("--capture", default="", help="--run 必填: .gfxr 路径 (须与 jsonl 同源)")
    ap.add_argument("--out-dir", default="", help="--run 的 dump 输出目录 (缺省 temp/gfxr/frame_dump_f<N>)")
    ap.add_argument("--scale", default="", help="透传 --dump-resources-scale (如 0.25 缩图省盘)")
    ap.add_argument("--immutable", action="store_true", help="透传 --dump-resources-dump-immutable-resources")
    ap.add_argument("--print-plan", action="store_true", help="打印五元组计划后退出 (不写 spec)")
    args = ap.parse_args()

    frames, submits = parse_jsonl(args.jsonl)
    if not 1 <= args.frame <= len(frames):
        raise SystemExit(f"错误: --frame {args.frame} 越界, jsonl 共 {len(frames)} 条 CB")
    frame = frames[args.frame - 1]

    # submit 组检查: 目标帧 CB 与更早 CB 合并在同一 submit 时, dump 挂超大命令缓冲会 GPU 超时
    submit = pick_submit(frame["bcb"], submits)
    prev = max([s for s in submits if s < submit], default=-1)
    grouped = [f2["bcb"] for f2 in frames if prev < f2["bcb"] < submit]
    if len(grouped) > 1:
        print(f"警告: CB {frame['bcb']} 与 {len(grouped) - 1} 条更早 CB 合并在 submit {submit} "
              f"(组内 CB: {grouped}); 实测此形态 dump 会触发 MoltenVK GPU 超时 device lost。"
              f"建议目标帧后移或重抓帧窗口 (目标帧前留 >=4 帧 lead-in)。", file=sys.stderr)

    selects = range(len(frame["passes"]))
    if args.passes:
        picks = {int(x) for x in args.passes.split(",")}
        selects = [i for i in selects if i in picks]
    elif not args.all_passes:
        selects = [i for i in selects if frame["passes"][i]["draws"]]  # 缺省只取有 draw 的 pass

    passes = [frame["passes"][i] for i in selects]
    spec, submit = build_spec(frame, passes, submits)

    if args.print_plan:
        for i, p in zip(selects, passes):
            d = p["draws"][-1] if p["draws"] else p["fullscreens"][0]
            kind = f"{len(p['draws'])} draws" if p["draws"] else "fullscreen"
            print(f"  pass{i}: [{p['begin']}..{p['end']}] {kind} -> Draw={d}")
        print(f"BeginCommandBuffer={frame['bcb']} QueueSubmit={submit}")
        return

    out = args.out or os.path.join(os.path.dirname(args.jsonl) or ".", f"frame_dump_spec_f{args.frame}.json")
    with open(out, "w") as f:
        json.dump(spec, f)
    print(f"spec 已写: {out} (CB {frame['bcb']}, {len(spec['Draw'][0])} 个 dump 点, submit {submit})")

    if not args.run:
        return

    if not args.capture:
        raise SystemExit("错误: --run 需要 --capture 指向同源 .gfxr")
    # replay 系工具 dlopen Vulkan loader, 与 capture_frames.sh 同款 SDK 发现
    sdk = os.environ.get("GFXRECON_SDK_ROOT") or ""
    if not sdk:
        import glob
        for d in sorted(glob.glob(os.path.expanduser("~/VulkanSDK/*")), reverse=True):
            if os.path.isfile(os.path.join(d, "macOS", "bin", "gfxrecon-replay")):
                sdk = d
                break
    if not sdk:
        raise SystemExit("错误: 未找到 VulkanSDK (gfxrecon-replay); 可用 GFXRECON_SDK_ROOT 显式指定")
    setup_env = os.path.join(sdk, "setup-env.sh")
    out_dir = args.out_dir or os.path.join("temp/gfxr", f"frame_dump_f{args.frame}")
    os.makedirs(out_dir, exist_ok=True)  # 坑 #1: 目录必须预建
    cmd = ["--dump-resources", out, "--dump-resources-dir", out_dir,
           "--dump-resources-dump-depth-attachment"]
    if args.immutable:
        cmd.append("--dump-resources-dump-immutable-resources")
    if args.scale:
        cmd += ["--dump-resources-scale", args.scale]
    shell = f'source "{setup_env}" >/dev/null 2>&1 && "$1/macOS/bin/gfxrecon-replay" "$2" "${{@:3}}"'
    r = subprocess.run(["bash", "-c", shell, "frame_dump", sdk, args.capture] + cmd,
                       capture_output=True, text=True)
    sys.stderr.write(r.stderr[-2000:] if r.returncode else "")
    if r.returncode:
        raise SystemExit(f"错误: gfxrecon-replay 退出码 {r.returncode} (超时/device lost 查上方输出)")

    # dr.json 摘要
    dr = os.path.join(out_dir, os.path.splitext(os.path.basename(args.capture))[0] + "_dr.json")
    with open(dr) as f:
        data = json.load(f)
    n = 0
    for entry in data[1:]:
        for c in entry.get("drawCallCommands", []):
            n += 1
            ca = [f"att{a['location']}:{a['format'].removeprefix('VK_FORMAT_')}@{a['subresources'][0]['dimensions'][0]}x{a['subresources'][0]['dimensions'][1]}" for a in c.get("colorAttachments", [])]
            da = c.get("depthAttachment")
            depth = (f"深度 {da['format'].removeprefix('VK_FORMAT_')}@{da['subresources'][0]['dimensions'][0]}x{da['subresources'][0]['dimensions'][1]}"
                     if da else "无深度")
            desc = c.get("descriptors", {})
            ndesc = sum(len(v) for v in desc.values()) if isinstance(desc, dict) else 0
            print(f"draw {c['drawIndex']}: {' '.join(ca) or '(无颜色附件)'} | {depth} | 描述符 {ndesc} 项")
    print(f"共 {n} 个 dump 点, 产物在 {out_dir}/ (报告 {dr})")


if __name__ == "__main__":
    main()
