# qrenderdoc --python 深度分析形态

以 RenderDoc 1.46 为基线；升级工具后整体重验本文的 API 形态。

`dcc_mcp_renderdoc` 的 runtime 函数覆盖浅层分析。帧内中间纹理、UBO 字节、像素归属和着色器单步
用 qrenderdoc 自带 Python 写脚本，这也是 MCP 深度工具的底层执行方式。

## 脚本桥接模板

qrenderdoc 使用自带 Python，只接受文件参数；stdin/heredoc 不作为入口。
母本见 [rd_probe.py](../scripts/rd_probe.py) 与 [rd_frame_report.py](../scripts/rd_frame_report.py)。

```python
import json, os, traceback

def main():
    out = {}
    try:
        import renderdoc as rd
        capture_file = os.environ["DCC_MCP_RENDERDOC_CAPTURE"]
        context = globals().get("pyrenderdoc")          # qrenderdoc 注入的上下文
        if context is None:
            raise RuntimeError("no pyrenderdoc")
        context.LoadCapture(capture_file, rd.ReplayOptions(), capture_file, False, True)

        def cb(controller):
            def walk(actions):
                for a in actions:
                    yield a
                    yield from walk(a.children)
            for a in walk(controller.GetRootActions()):
                if a.flags & rd.ActionFlags.Drawcall:
                    controller.SetFrameEvent(int(a.eventId), True)
                    pipe = controller.GetPipelineState()

        context.Replay().BlockInvoke(cb)
        context.CloseCapture()
    except BaseException as exc:
        out["error"] = "%r\n%s" % (exc, traceback.format_exc())
    finally:
        with open(os.environ["DCC_MCP_RENDERDOC_RESOURCE_STATUS"], "w") as f:
            json.dump(out, f)
        # 结果走文件；stdout 不作为结果通道

main()
raise SystemExit(0)
```

运行：

```bash
DCC_MCP_RENDERDOC_CAPTURE='<capture.rdc>' \
DCC_MCP_RENDERDOC_RESOURCE_STATUS='<workspace>/temp/out.json' \
'<renderdoc>/qrenderdoc.exe' --python '<script 绝对路径>'
```

之后用系统 Python 读 `out.json`。

## 1.46 API 形态

| 主题 | 形态 |
|---|---|
| 输出目标 | `pipe.GetOutputTargets()` 返回 `Descriptor`，字段 `.resource` |
| PS/VS 只读资源 | `pipe.GetReadOnlyResources(stage, True)`，元素 `.descriptor.resource` |
| `PixelModification` | 失败标志是属性（`backfaceCulled/depthTestFailed/shaderDiscarded/scissorClipped/viewClipped/...`），通过标志是方法 `Passed()`；数值在 `preMod/postMod/shaderOut` 的 `.col.floatValue` |
| `ResourceId` | 不能直接构造；从 `GetResources()/GetTextures()` 查表拿原生对象，比较用 `int()` |
| `GetTextures()` | `SetFrameEvent` 之后调用仍返回全量；回调开头快照是防御写法 |
| 深度读数 | D32 经 `GetTextureData` 返回原始 f32 行主序；深度判读以原始数据为准 |
| post-VS | `controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)` 返回 `MeshFormat`（vertexResourceId/vertexByteOffset/vertexByteStride），再 `GetBufferData` 解析 float4 |
| DebugPixel | 三参 `DebugPixel(x, y, rd.DebugPixelInputs())`；trace 无预置 steps |

## DebugPixel 配方

- 签名：`tr = controller.DebugPixel(x, y, rd.DebugPixelInputs())`；sample/primitive/view 选择子在
  `DebugPixelInputs` 里填。
- trace 无预置 steps：`ShaderDebugState` 逐批经 `controller.ContinueDebug(tr.debugger)` 驱动收集，
  空批表示结束；`trace.debugger` 缺失（event 未覆盖该像素）时无法单步。
- 每步 `ShaderDebugState` 无 `.vars`：变量变化在 `.changes`，每项是 `.before`/`.after` 两个
  `ShaderVariable`，取值走 `.value.f32v`/`.u32v`。
- `tr.constantBlocks` 是扁平 `ShaderVariable` 列表，每项一个 cbuffer 变量。
- 用完调用 `controller.FreeTrace(tr)`，用 try/except 包住，逐 trace 释放。
- 读法：先驱动完全部 steps，再按嫌疑变量名（大小写不敏感子串）过滤 changes；
  SPIR-V 调试下中间变量多为 `_4`/`_6` 内部名，常量块与 sourceVars 命中率更高。

## 帧内分析经验

- post-VS + UBO/VB 原始数据能核对真实 clip 坐标；把 GPU 数据拷回 CPU，用同一矩阵乘法复算投影，
  与渲染结果对照，区分 CPU 数据、shader 布局和约定问题。
- 纹理坐标/offset 的图像相关性要警惕周期棋盘格假阳性：错误平移也可得 0.9+ 相关性，
  用轮廓、独特物体等非周期特征复核。
- 全屏 pass 覆盖异常时先查全屏三角形顶点生成和 viewport；深度/阴影全为清值先查 mesh draw 是否零覆盖。
- 画面上下颠倒或阴影错位时，对照 NDC Y 方向、深度范围和投影矩阵约定，分别核对生成端与重建端。

## 案例：延迟渲染全黑的取证顺序

现象：最终画面只有天空渐变，其余全黑。

取证链：

1. action 树和 draw 计数正常，排除“没有画”。
2. 深度原始采样全为清值，阴影图也为清值，说明 mesh draw 零覆盖。
3. gbuffer 像素历史只有 Clear 记录，佐证零覆盖而不是深度测试失败。
4. UBO dump 发现 view 矩阵平移在错误位置，相机基向量放错行列，旋转被转置，投影 `clip.w` 变负，
   全部被裁剪。
5. post-VS 发现全屏三角形顶点范围不足，全屏 pass 只覆盖左上角。
6. 最终图像上下颠倒与阴影错位，追到 NDC Y 方向与深度约定的转换点。

结论：先按“结构正常 → 覆盖为零 → 矩阵/顶点数据 → 坐标约定”逐层排除，再改代码。
