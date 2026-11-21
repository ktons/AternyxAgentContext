---
name: win-crash-debug
description: Windows 崩溃、AV、abort、异常退出与 minidump 排查。有引擎 dump 先离线分析，无现场时用调试器取栈；macOS 不适用。
---

# Windows 崩溃取证与修复

职责：取得可复现、可符号化的崩溃现场，定位根因；修复按用户授权范围执行。模块机制归对应文档；
从架构入口定位模块，引擎自产现场的机制见 `docs/modules/module-core-struct.md`。通用开发调度遵循
`AGENTS.md`，明确要求执行已有 roadmap 时使用 `development-workflow`。

## 标准路径

1. 保留复现命令、CWD、退出码、最后日志和构建状态，暂停当前功能实现。
2. 先查项目 temp/crash 的 meta 和 minidump；有现场先离线分析，无现场或挂死才活体复现。
3. 发现实际调试器和符号，优先 cdb 原生 PDB，必要时用 lldb。核对 dump、exe 与符号的版本匹配；
   仅分析已有现场时不要求重建。获修复授权后后台构建受影响 target，不用旧 exe 得出当前实现结论；
   编译器环境按 `project-workflow` 发现。用户要求只读时只探查文档、源码与已有现场，不构建或修改代码。
4. 调试器批处理取异常上下文与调用栈，给运行窗口和超时；abort 类预下 CRT 断点避免弹窗阻塞。
   定位真实调用链与生命周期，不把栈顶位置直接当根因。
5. 获修复授权后向前修复，保留用户及任务已有改动；陈旧 obj/头依赖、锁文件等先核实环境证据。
6. 修复后重验原复现与受影响 suite；仅排查时交付原因、证据与修复建议。确认调试器与目标均退出。

## 常用命令

Windows PowerShell，从仓库根执行。工具路径先发现，dump、exe、符号必须对应同一构建；离线分析不先重建：

```powershell
& "<实际cdb.exe>" -z "<minidump绝对路径>" -c ".ecxr; k; q"
```

lldb 活体复现需调用方设置实际 CWD 和外层总超时，条件式命令与脚本限制见操作参考；不能仅凭其参数名推断行为。

断点配方、工具发现、自检与故障经验查 [操作参考](references/operations.md)。
缺工具时发现可用替代或报告所需安装，不假定某个 agent 宿主提供包装脚本。
新工具经验更新 reference，模块约束更新模块文档，不自动新建或改写 skill。
