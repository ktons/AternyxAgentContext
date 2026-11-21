# GFXReconstruct 工具操作（macOS）

本文记录 macOS + Vulkan SDK 下 GFXReconstruct 的通用操作。路径用占位符：
`<VulkanSDK>`、`<workspace>`、`<target>`。

## SDK 与工具发现

工具在 Vulkan SDK 的 `macOS/bin/` 下：

| 工具 | 用途 |
|---|---|
| `gfxrecon-capture-vulkan.py` | 拉起目标并抓帧 |
| `gfxrecon-convert` | `.gfxr` 转 jsonl 或其他格式 |
| `gfxrecon-replay` | replay capture、dump resources、裁剪 |
| `gfxrecon-info` | 查看 capture 元信息 |

发现顺序：

1. `GFXRECON_SDK_ROOT` 显式指定；
2. `~/VulkanSDK/*/macOS/bin/gfxrecon-capture-vulkan.py` 中取最高版本；
3. 把 `macOS/bin` 加入 `PATH`。

[capture_frames.sh](../scripts/capture_frames.sh) 已实现上述发现，并在找不到时报错。

## 抓帧

```bash
'<skill>/scripts/capture_frames.sh' 100-130 "$PWD/temp/gfxr/cap" \
  "$PWD/build/bin/<target>" --project "$PWD/<project>" --frames 300
```

关键约束：

- **窗口模式**：capture 以 `vkQueuePresentKHR` 计帧。离屏目标没有 present，抓帧不前进。
- **绝对路径**：程序和 `-o` 前缀用绝对路径；脚本切到程序目录后再启动。
- **帧区间**：`-f 100-130` 这类区间必传；不传会抓全部帧。
- **完成标记**：stdout 出现 `Finished recording` 才算成功（版本不同前缀略有差异，脚本按该子串匹配）。
  注入失败可能是静默的，没有标记就按失败处理。
- **参数顺序**：程序作为第一个参数且为绝对路径，其后是程序参数；脚本切到程序目录后再启动。
- **后缀补齐**：`--no-file-timestamp` 下产物可能不带 `.gfxr`，脚本统一改名。
- **进程回收**：抓帧结束确认目标退出；残留实例会占用 GPU 和端口。

## 转 jsonl

```bash
gfxrecon-convert --format jsonl <capture.gfxr>
```

每条记录有 `index` 和 `function.name`。渲染结构分析依赖以下调用：

- `vkBeginCommandBuffer` / `vkEndCommandBuffer`
- `vkCmdBeginRenderPass` / `vkCmdEndRenderPass`
- `vkCmdDraw` / `vkCmdDrawIndexed`
- `vkQueueSubmit`

jsonl 是后续 pass 提取与 dump spec 的唯一索引来源；索引只对同一条 jsonl / 同一个 `.gfxr` 有效。

## 提取 pass 与 draw 索引

```bash
python3 '<skill>/scripts/extract_passes.py' <capture.jsonl> --frame 5
python3 '<skill>/scripts/extract_passes.py' <capture.jsonl> --all-frames
```

`--frame N` 指第 N 条 `vkBeginCommandBuffer`，不是 swapchain 帧号。输出每个 pass 的：

- `begin` / `end` 调用 index；
- draw 列表与 fullscreen draw 列表；
- 首末 draw index；
- 归属该命令缓冲的 `vkQueueSubmit`。

录制与提交可能解耦：首个 submit 可能排在多条命令缓冲之后。脚本把 submit 回填给此前尚无 submit 的帧；
dump 时目标帧和前面的帧落在同一个超大 submit 组会触发 MoltenVK GPU 超时，改抓更晚的帧窗口，
目标帧前留至少 4 帧 lead-in。

## dump 资源

```bash
# 只生成 spec 并打印计划
python3 '<skill>/scripts/frame_dump.py' <capture.jsonl> --frame 5 \
  --out temp/gfxr/spec_f5.json --print-plan

# 生成 spec 并 replay dump
python3 '<skill>/scripts/frame_dump.py' <capture.jsonl> --frame 5 --run \
  --capture <capture.gfxr> --out-dir temp/gfxr/dump_out --passes 0,1,3
```

`frame_dump.py` 把“提 pass → 配对 submit → 写 JSON spec → replay → 读 `dr.json`”固化成一步：

- dump 点取每个所选 pass 的最后一个 draw；fullscreen-only pass 用其 `vkCmdDraw`；
- 输出目录自动预建；
- `--run` 需要 replay 工具的 SDK 环境，或 `GFXRECON_SDK_ROOT`；
- `--all-passes` 把 fullscreen-only pass（post 链）也纳入 dump；`--scale` / `--immutable` 透传给 dump。

也可以直接调用：

```bash
gfxrecon-replay --dump-resources <spec.json> <capture.gfxr>
```

dump 生成的附件是 BMP，路径和命名写在 `dr.json`。

## 读附件

```bash
python3 '<skill>/scripts/read_attachment.py' <attachment.bmp> \
  --format R16G16B16A16_SFLOAT --point 1280,1300 --point 400,1100

python3 '<skill>/scripts/read_attachment.py' <attachment.bmp> \
  --scan-y 830 --x0 860 --x1 1150 --step 20

python3 '<skill>/scripts/read_attachment.py' <attachment.bmp> --histogram --cell 4
```

BMP 为 bottom-up + BGR(A) 文件序；`--format` 只决定通道语义与个数，不改变字节序。
`R16G16B16A16_SFLOAT` 等浮点附件被 gfxrecon 量化为 8bit BMP，值只有 1/255 精度；
用 ambient、天空渐变等已知参照标定后再读其他区域。预览工具对 32bpp 浮点附件的转换会产生假色，
数值判读以 `read_attachment.py` 的原始字节为准。

## 常见坑

- **抓帧不前进**：目标跑在离屏模式，或呈现被禁用；改窗口模式。
- **找不到程序**：`gfxrecon-capture-vulkan.py` 切 CWD 后按相对路径找不到目标；程序和输出都用绝对路径。
- **dump 全在一条超大 command buffer**：录制/提交解耦导致；改抓目标帧前有足够 lead-in 的窗口。
- **附件颜色异常**：预览工具转换浮点 BMP 的假色；用原始字节读数复核。
- **索引错位**：换了 capture 或重新 convert 后仍用旧 index；每次重新提取。

## 参考

- [抓帧脚本](../scripts/capture_frames.sh)
- [pass 提取](../scripts/extract_passes.py)
- [dump 封装](../scripts/frame_dump.py)
- [附件读取](../scripts/read_attachment.py)
