# RenderDoc 工具操作与生命周期

本文记录 RenderDoc 1.46 的通用操作形态。安装路径、Python 位置和 MCP 配置按实际环境发现；命令中的
`<renderdoc>`、`<python>`、`<workspace>` 是占位符。

## 工具链

| 工具 | 用途 |
|---|---|
| `renderdoccmd.exe` | headless 抓帧、capture 转换、注入目标 |
| `qrenderdoc.exe --python <script>` | 深度分析，使用自带 Python，可批量读取帧状态 |
| `<python>` | 项目抓帧驱动、结果 JSON 解析、图片后处理 |

把 RenderDoc 安装目录加入 `PATH`，或显式设置：

```bash
export DCC_MCP_RENDERDOC_CMD='<renderdoc>/renderdoccmd.exe'
export DCC_MCP_RENDERDOC_AUTO_DOWNLOAD=0   # 已安装 RenderDoc 时阻止适配器下载副本
```

## 抓帧

抓帧入口由项目驱动提供：构建目标、启动参数、工程/场景和就绪信号因项目而异。
[do_capture_template.py](../templates/do_capture_template.py) 给出推荐边界。

抓帧时机：

- 目标进程在创建图形设备前挂上 RenderDoc；对已经建好设备的进程再附加通常失败。
- 用 `subprocess.Popen` 拉起目标后立即注入，或让驱动在启动命令里挂载。
- 记录目标 PID、可执行路径、启动参数、构建版本和 capture 路径。
- 目标窗口加载完场景后再触发 capture；加载画面和首帧不作为问题证据。
- 抓完先让目标正常退出；超时再按记录的 PID 强制结束。

已有进程附加的时序：

```python
proc = subprocess.Popen(target_argv, cwd=run_dir)
# 立即调用抓帧适配器，传入 proc.pid 和输出前缀
```

附加已建设备的进程会报 `Target Control failed: timed out waiting for RenderDoc NewCapture`；
Vulkan 无法对已加载实例补挂钩。此时改为重新拉起目标。

## qrenderdoc 批量分析

qrenderdoc 每次启动都要加载并 replay capture，深度分析按“一次启动回答一轮问题”组织。

```bash
DCC_MCP_RENDERDOC_CAPTURE='<capture.rdc>' \
RD_OUT='<workspace>/temp/rd_report' \
DCC_MCP_RENDERDOC_RESOURCE_STATUS='<workspace>/temp/rd_report/status.json' \
'<renderdoc>/qrenderdoc.exe' --python '<skill>/scripts/rd_frame_report.py'
```

`rd_frame_report.py` 产出：

- `status.json`：action 树、每个 drawcall 的 RT/PS 只读资源/viewport、纹理清单；
- `tex_<res>_<w>x<h>.png`：帧结束时大于 4px 的纹理内容。

批量探针：

```bash
DCC_MCP_RENDERDOC_CAPTURE='<capture.rdc>' \
RD_PROBE_SPEC='<workspace>/temp/probe_spec.json' \
DCC_MCP_RENDERDOC_RESOURCE_STATUS='<workspace>/temp/probe_result.json' \
RD_OUT='<workspace>/temp/rd_probe' \
'<renderdoc>/qrenderdoc.exe' --python '<skill>/scripts/rd_probe.py'
```

`rd_probe.py` 的 spec 覆盖 action 树、draw 绑定、管线状态、纹理导出、纹理/ buffer 原始读数、
像素历史、PS 单步、post-VS 顶点和资源生命周期。把一轮排查的全部问题写进同一个 spec，
只付一次 replay 成本。

## 分析路径

1. **最终画面**：先确认呈现图对应目标 view；离屏渲染的目标图可能在颜色附件。
2. **阶段附件**：按 pass 找异常首次出现的阶段，记录 event、资源和格式。
3. **drawcall 与绑定**：用 action 树和 draw 列表定位可疑 draw，读 RT、PS 只读资源、viewport/scissor。
4. **资源内容**：纹理导出看形态，原始字节读数值；浮点附件和深度优先读原始数据。
5. **像素历史**：确认像素由哪个 draw/primitive 写入，检查 depth/stencil/blend 的通过或失败原因。
6. **几何**：post-VS 顶点核对 clip 坐标，和 CPU 侧矩阵复算对照。
7. **shader 单步**：按可疑变量名过滤 `changes`，结合常量块和 sourceVars 定位计算环节。

## 证据与进程

- 每轮保存 `.rdc`、探针 JSON、纹理 PNG 和结论；结论写清 capture、event、资源、格式和读数。
- 工具截图和纹理查看器用于定性观察；精确结论用采样、状态读数或脚本比较。
- 画面观察、假说和已证实原因分开记录；跨 capture 比较时重新核对 draw 与资源 id。
- 只回收本会话拉起的进程：先按 PID 优雅退出，超时再强制结束；结束前核实 PID 仍是同一实例。
- 残留目标会锁住 exe，导致下一次链接失败；收口时检查并清理。

## 可选 MCP 链路

`dcc-mcp` 可用时，`ensure_renderdoc_mcp.py` 探测本机 renderdoc 适配器；没有实例则以分离进程启动
`python -m dcc_mcp_renderdoc`，并等待实例注册。

```bash
<python> '<skill>/scripts/ensure_renderdoc_mcp.py'
```

MCP 适合单点追问和交互式探查；批量问题优先走 `rd_probe.py`，避免每个问题重启一次 qrenderdoc。
MCP 不可用时直接用 qrenderdoc 脚本，分析能力等价。

## 参考

- [qrenderdoc Python 形态](qrenderdoc-python.md)
- [抓帧驱动模板](../templates/do_capture_template.py)
