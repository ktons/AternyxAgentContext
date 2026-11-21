#!/usr/bin/env python3
"""从 gfxrecon-convert --format jsonl 输出提取 render pass 结构与 dump 五元组。

用法:
  python3 extract_passes.py <capture.jsonl> [--frame N] [--all-frames]

输出:
  - 每条命令缓冲的 render pass 序列: pass 序号 / begin-end index / draw 数与
    首末 draw index / fullscreen draw index —— 即 dump-resources 五元组的全部原料
    (BeginCommandBuffer / BeginRenderPass / EndRenderPass / Draw / QueueSubmit)。
  - 不带 --frame 时只解析第 1 条命令缓冲 (trimmed capture 首帧即排查目标帧)。

注意: 索引只对生成它的同一条 jsonl / 同一个 .gfxr 有效, 换 capture 必须重新提取。
"""
import argparse
import json
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--frame", type=int, default=1, help="第几条命令缓冲 (默认 1)")
    ap.add_argument("--all-frames", action="store_true", help="解析全部命令缓冲")
    args = ap.parse_args()

    frame = 0
    bcb = None
    cur = None
    passes = []
    submits = []
    frames = []
    done = False

    with open(args.jsonl) as f:
        for line in f:
            rec = json.loads(line)
            idx = rec.get("index")
            fn = rec.get("function", {}).get("name", "")
            if fn == "vkQueueSubmit" and frames:
                # submit 可能覆盖此前多条命令缓冲 (录制/提交解耦): 回填所有尚无
                # submit 的帧 (实测首个 submit 排在第 4 条命令缓冲之后)
                for fr in frames:
                    if not fr[3]:
                        fr[3].append(idx)
                if done and all(fr[3] for fr in frames):
                    break
                continue
            if fn == "vkBeginCommandBuffer":
                frame += 1
                if done and submits:
                    break
                if cur is not None:  # 未闭合 pass 视为已结束
                    passes.append(cur)
                    cur = None
                bcb = idx
                passes = []
                submits = []
                frames.append((frame, bcb, passes, submits))
                if not args.all_frames and frame == args.frame:
                    done = True
                continue
            if not frames:
                continue
            if fn == "vkCmdBeginRenderPass":
                cur = {"begin": idx, "draws": [], "fullscreens": []}
            elif fn == "vkCmdEndRenderPass":
                if cur is not None:
                    cur["end"] = idx
                    passes.append(cur)
                    cur = None
            elif fn == "vkCmdDrawIndexed" and cur is not None:
                cur["draws"].append(idx)
            elif fn == "vkCmdDraw" and cur is not None:
                cur["fullscreens"].append(idx)

    for no, bcb, passes, submits in frames:
        print(f"== frame {no}: BeginCommandBuffer={bcb}  QueueSubmit={submits}")
        for i, p in enumerate(passes):
            if p["draws"]:
                kind = f"{len(p['draws'])} draws ({p['draws'][0]}..{p['draws'][-1]})"
                last_draw = p["draws"][-1]
            elif p["fullscreens"]:
                kind = f"{len(p['fullscreens'])} fullscreen {p['fullscreens']}"
                last_draw = p["fullscreens"][0]
            else:
                kind = "0 draws"
                last_draw = None
            print(f"  pass{i}: [{p['begin']}..{p.get('end', '?')}] {kind}")
        print("  -- dump 五元组建议 (首个含 draw 的 pass):")
        for i, p in enumerate(passes):
            if p["draws"] or p["fullscreens"]:
                d = p["draws"][-1] if p["draws"] else p["fullscreens"][0]
                print(f"    BeginCommandBuffer={bcb},Draw={d},"
                      f"BeginRenderPass={p['begin']},EndRenderPass={p['end']},"
                      f"QueueSubmit={submits[0] if submits else '?'}")
                break


if __name__ == "__main__":
    sys.exit(main())
