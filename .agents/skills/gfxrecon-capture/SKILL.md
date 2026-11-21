---
name: gfxrecon-capture
description: macOS Vulkan GFXReconstruct 抓帧与分析：按帧区间抓取窗口程序、转 jsonl、提取 render pass 与 draw 索引、dump 附件并读取原始像素。需要定位 Vulkan 渲染异常或核对 MoltenVK 帧内容时使用；根因分析方法见 graphics-debug-methodology。
---

# GFXReconstruct 抓帧与分析

职责：在 macOS 上用 GFXReconstruct 抓取 Vulkan 帧，建立“帧 → render pass → draw → 资源”的索引，
读取附件和原始像素，并在修改后重抓复验。

## 标准流程

1. **准备抓帧条件**：使用窗口模式目标（capture 以 `vkQueuePresentKHR` 计帧），确认 SDK、
   目标程序、工程/场景、帧区间和构建产物都是待查版本。
2. **抓帧**：用 [capture_frames.sh](scripts/capture_frames.sh) 固定帧区间、绝对路径和 CWD；
   确认 stdout 出现 `[gfxrecon] INFO - Finished recording`，再使用产物。
3. **转 jsonl**：`gfxrecon-convert --format jsonl <capture.gfxr>`，得到逐调用索引。
4. **提 pass 结构**：用 [extract_passes.py](scripts/extract_passes.py) 列出每条命令缓冲的
   render pass、draw 数与首末 draw 索引。
5. **dump 目标 pass**：用 [frame_dump.py](scripts/frame_dump.py) 生成 dump spec，
   一次 replay 导出所选 pass 的附件。
6. **读附件**：用 [read_attachment.py](scripts/read_attachment.py) 按原始字节读 BMP；
   浮点附件已量化为 8bit，读值前先用已知参照标定。
7. **复验与收口**：修改后按相同帧区间重抓，核对异常 pass/draw 与最终画面；回收本会话拉起的进程。

## 抓帧

```bash
GFXRECON_SDK_ROOT='<VulkanSDK>' \
'<skill>/scripts/capture_frames.sh' 100-130 "$PWD/temp/gfxr/cap" \
  "$PWD/build/bin/<target>" --project "$PWD/<project>" --frames 300
```

约束：

- 目标用窗口模式运行；离屏程序没有 `vkQueuePresentKHR`，按呈现帧计数的抓取不会前进。
- 帧区间必传；不传会抓全部帧，文件体积不可控。
- 程序和输出前缀用绝对路径；包装脚本在切换 CWD 前解析。
- 产物可能不带 `.gfxr` 后缀，脚本会补齐；后续命令使用补齐后的路径。
- SDK 发现顺序：`GFXRECON_SDK_ROOT` → `~/VulkanSDK/*/macOS/bin/gfxrecon-capture-vulkan.py` 的最高版本。

## 建立索引

```bash
gfxrecon-convert --format jsonl <capture.gfxr>
python3 '<skill>/scripts/extract_passes.py' <capture.jsonl> --frame 5
```

输出每条命令缓冲的 pass 序列：

- `BeginCommandBuffer` / `BeginRenderPass` / `EndRenderPass`；
- 每个 pass 的 draw 数与首末 draw index；
- fullscreen draw index；
- 归属该命令缓冲的 `vkQueueSubmit`。

索引只对生成它的同一条 jsonl / 同一个 `.gfxr` 有效；换 capture 必须重新提取。

## dump 与读取

```bash
# 生成 spec 并查看计划
python3 '<skill>/scripts/frame_dump.py' <capture.jsonl> --frame 5 \
  --out temp/gfxr/spec_f5.json --print-plan

# 一次 replay dump 选中的 pass
python3 '<skill>/scripts/frame_dump.py' <capture.jsonl> --frame 5 --run \
  --capture <capture.gfxr> --out-dir temp/gfxr/dump_out --passes 0,1,3

# 读附件原始字节
python3 '<skill>/scripts/read_attachment.py' <attachment.bmp> \
  --format R16G16B16A16_SFLOAT --point 1280,1300 --point 400,1100
```

`frame_dump.py` 的 dump 点取每个 pass 的最后一个 draw；fullscreen-only pass 用其 `vkCmdDraw`。
目标帧和前面的帧合并在同一个超大 submit 组时，dump 可能触发 MoltenVK GPU 超时；此时改抓目标帧
之前留至少 4 帧 lead-in 的窗口。

`read_attachment.py` 按文件字节序读 BMP：文件序为 B,G,R[,A]，`--format` 只决定通道语义与个数。
浮点附件被 gfxrecon 量化为 8bit BMP，值只有 1/255 精度；用 ambient、天空渐变等已知参照标定后
再读其他区域。预览工具对 32bpp 浮点附件的转换会产生假色，数值判读以原始字节为准。

## 证据与收口

- 保存 `.gfxr`、jsonl、pass 索引、dump spec、附件 BMP 和读数 JSON；结论写清 frame/pass/draw、
  资源、格式和坐标。
- 画面观察用于定位 pass，精确结论用原始字节读数或脚本比较。
- capture 和 replay 进程只回收本会话拉起的实例；抓帧结束确认目标正常退出。
- 工具安装、SDK 发现和常见坑见 [工具操作](references/operations.md)。

## 参考

- [工具操作与 macOS 约束](references/operations.md)
