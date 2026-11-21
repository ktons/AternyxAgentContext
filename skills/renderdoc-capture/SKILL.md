---
name: renderdoc-capture
description: Windows RenderDoc 抓帧与帧内分析：抓取目标进程、查看呈现图与阶段附件、批量查询 drawcall/资源/绑定/像素历史/几何/shader 单步，并在修改后重抓复验。需要定位 GPU 渲染异常或核对某一帧实际执行内容时使用；根因分析方法见 graphics-debug-methodology。
---

# RenderDoc 抓帧与分析

职责：把一个可复现的渲染问题固化成 `.rdc`，在帧内定位异常事件、资源和管线状态，并让结论可复验。
本 skill 维护工具操作与 qrenderdoc 脚本；异常归因、代码阅读和修复决策按 `graphics-debug-methodology` 推进。

## 标准流程

1. **准备可复现条件**：固定目标程序、启动参数、工程/场景、分辨率、帧号和构建产物；确认抓帧使用的是包含待查改动的新二进制。
2. **抓帧**：用项目抓帧驱动拉起目标并触发一次 capture；目标进程在创建图形设备前挂上 RenderDoc。
   捕获完成后记录 `.rdc` 路径、目标进程、启动参数、构建版本和抓帧帧号。
3. **先看最终画面**：确认呈现的是目标 view；再看各阶段附件，找出异常首次出现的阶段。
   离屏场景可能在颜色附件而不是交换链，先辨认目标 view 与呈现图。
4. **帧内定位**：围绕可疑阶段查 drawcall、输入输出资源、绑定、viewport/scissor 和管线状态；
   按问题选择像素历史、纹理/ buffer 采样、post-VS 几何、shader 单步。
5. **批量取证**：一轮排查把问题写进 `rd_probe.py` 的 JSON spec，一次 replay 全部回答；
   需要整帧概览时用 `rd_frame_report.py` 生成 action 树、draw 绑定和纹理清单。
6. **修改后重抓**：按正常出口构建并重新导入资产，用相同场景、相机、帧号和参数复抓，核对异常点与最终画面。
7. **收口**：保存 capture、读数 JSON、对比图和结论；回收本会话拉起的引擎、qrenderdoc 与分析进程。

## 抓帧驱动

抓帧入口由项目提供，因为构建命令、目标名、工程参数和就绪判定因项目而异。
[do_capture_template.py](templates/do_capture_template.py) 给出驱动边界：构建目标、拼启动参数、等待就绪、触发 capture、回收进程。
复制模板到项目脚本目录，实现其中的钩子；通用分析脚本留在本 skill。

抓帧时记录并只回收本会话拉起的进程：先按 PID 优雅退出，超时再强制结束。
按进程名批量结束会误伤用户或其他会话的实例。

## 通用分析脚本

### 一次性帧报告

```bash
DCC_MCP_RENDERDOC_CAPTURE='<capture.rdc>' \
RD_OUT='<workspace>/temp/rd_report' \
DCC_MCP_RENDERDOC_RESOURCE_STATUS='<workspace>/temp/rd_report/status.json' \
'<renderdoc>/qrenderdoc.exe' --python '<skill>/scripts/rd_frame_report.py'
```

产出 `status.json`：action 树、每个 drawcall 的 RT/PS 只读资源/viewport、纹理清单；
同时导出帧结束时大于 4px 的纹理 PNG。

### 批量探针

```bash
DCC_MCP_RENDERDOC_CAPTURE='<capture.rdc>' \
RD_PROBE_SPEC='<workspace>/temp/probe_spec.json' \
DCC_MCP_RENDERDOC_RESOURCE_STATUS='<workspace>/temp/probe_result.json' \
RD_OUT='<workspace>/temp/rd_probe' \
'<renderdoc>/qrenderdoc.exe' --python '<skill>/scripts/rd_probe.py'
```

`RD_PROBE_SPEC` 的 `probes` 数组支持：

| type | 用途 | 关键参数 |
|---|---|---|
| `actions` | action 树 | `name_filter`, `max` |
| `draws` | 每个 draw 的绑定与 viewport/scissor | `eids`, `max_draws` |
| `state` | 任意 event 的管线状态快照 | `eids` |
| `textures` | 导出纹理 PNG | `res`, `all_over_px`, `event` |
| `sample` | 纹理原始读数 | `event`, `points` |
| `buffer` | buffer 原始字节 | `items`, `as` |
| `history` | 像素历史与最后通过者 | `res`, `points`, `pick` |
| `debug_pixel` | PS 单步与变量过滤 | `event`, `points`, `vars_filter`, `max_steps` |
| `postvs` | post-VS 顶点 clip 坐标 | `eids`, `vertices` |
| `usage` | 资源全生命周期 | `res` |

探针按顺序执行；`event` 字段先 `SetFrameEvent` 再取数，历史与单步类探针填目标 draw 的 event id。
索引只对同一条 capture 有效，换 capture 后重新生成 spec。

## 工具发现

- `renderdoccmd.exe` 与 `qrenderdoc.exe` 通常在 RenderDoc 安装目录；把安装目录加入 `PATH`，或显式设置路径。
- `DCC_MCP_RENDERDOC_CMD` 指向 `renderdoccmd.exe`，`DCC_MCP_RENDERDOC_AUTO_DOWNLOAD=0` 可阻止适配器联网下载副本。
- qrenderdoc 使用自带 Python；脚本只接受文件参数，stdin/heredoc 不作为入口。
- `dcc-mcp` 可用时，`ensure_renderdoc_mcp.py` 探测并拉起本机 renderdoc 适配器；不可用时直接用 qrenderdoc 脚本，分析能力等价。

## 版本与证据

- 以 RenderDoc 1.46 的 API 形态为基线；升级工具后先重验 [qrenderdoc Python 形态](references/qrenderdoc-python.md)。
- 画面观察用于定位阶段，精确结论用采样、状态读数或脚本比较；记录 capture、event、资源、格式和读数。
- 工具截图/纹理查看器用于看形态，浮点附件和深度优先读原始字节；格式、通道、色彩空间和事件时点写进证据。
- 把 capture、JSON、PNG 和结论留在 `temp/`；机制结论回写模块文档，工具坑更新 [工具操作](references/operations.md)。

## 参考

- [工具操作与生命周期](references/operations.md)
- [qrenderdoc Python 形态](references/qrenderdoc-python.md)
- [抓帧驱动模板](templates/do_capture_template.py)
