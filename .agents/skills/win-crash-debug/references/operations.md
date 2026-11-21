# Windows 崩溃操作参考

先发现本机工具：可在 PowerShell 用 `Get-Command cdb,lldb -ErrorAction SilentlyContinue`，或查 Windows SDK
Debuggers/x64 与实际 LLVM 安装目录。下面固定路径、工具版本均为历史机器快照；构建在有效 vcvars 环境
直接运行 cmake，不依赖某个 agent 宿主的包装器。按 PID 回收本会话目标，保留用户自行启动的实例。

## 现场与符号

现场目录 `<projectPath>/temp/crash/crash-<pid>-<unix秒>/` 的 meta 可定位日志、帧号、线程与内存水位；
机制归 `docs/modules/module-core-struct.md`，离线取栈命令在 SKILL.md。macOS 的 stacktrace 报告头带 atos 命令，
不需要调试器。无现场或 hang 才活体复现。

## 工具链（历史机器快照，执行前先发现实际路径）

| 工具 | 路径 / 说明 |
|---|---|
| cdb (首选) | **已安装**: `C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe` (Windows SDK 10, 实测 10.0.28000.2957 可用)。Git Bash 路径 `"/c/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe"`。若重装系统/SDK 后缺失: 管理员跑 `winsdksetup.exe /features OptionId.WindowsDesktopDebuggers /q /norestart` 或 SDK 安装器勾选 "Debugging Tools for Windows"。**VS Installer 里的 "C++/CLI 支持" 组件与调试器无关** (那是托管/本机混合开发), VS 各版本都不通过它分发 cdb |
| lldb (备选) | `F:/DevKit/LLVM/bin/lldb.exe` (LLVM 21.1.2, Windows 原生调试引擎) + 系统 Python 调 `scripts/lldb_stack.py` (仅标准库), 崩溃时退出码 2 / 正常退出 0 / 环境错误 1 |
| 自检程序 | `scripts/mini_crash.c`——空指针解引用三层调用, 验证抓栈链路本身可用 |

## 活体断点与窗口

**活体 cdb 模板**： 被测进程 CWD 用子 shell 切 (`AternyxMain` 用 `build/bin`, `-p` 指工程目录), 输出重定向落日志:

   ```bash
   (cd build/bin && "/c/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe" \
     -lines -c "bp <断点>; g; .echo ===HIT===; k; q" \
     AternyxMain.exe -p "$PWD/../../Test/Projects/General") > temp/crash_cdb.log 2>&1
   ```

   - `-lines`: 栈帧带源码行; `-c` 串在初始断点执行: 下断点 → `g` → 命中任意断点/二次异常后 `g` 返回, 继续执行 `k`(带符号栈) → `q`
   - **崩溃类型选断点**:
     - AV/硬异常: 不用下断点, `g` 遇未处理异常自动停, `k` 即栈。可用 `sxe 0xc0000005` 提升为首断
     - **abort/弹窗类 (Debug Error! "abort() has been called")**: 必须预下断点抢在 CRT 弹窗之前, 否则 MessageBox 阻塞进程、抓不到栈:
       `bp ucrtbased!_wassert; bp ucrtbased!abort; g; k; q` (Release/MT 线程没有 ucrtbased 时改下 `bp abort`)
     - 未捕获 C++ 异常 → terminate → abort: 上面断点即覆盖 (terminate 在栈展开前调用, `k` 里能看到 throw 现场: `_CxxThrowException → thrower`)
   - **退出码判读**: 正常运行不崩时 `g` 永不返回——用 `timeout N cdb ...` 兜底 (exit=124 即窗口期无崩溃, 配合 grep 日志确认无 `EH exception`/断点命中); 崩溃时命令串跑完自己 `q` 退出
   - 交互式常驻目标 (editor) 复抓验证没有"退出码 0"可看, 以"窗口期断点零命中 + 关键功能日志正常"为准

`this=nullptr` 等栈帧先查调用链、重入和销毁顺序；修复要解释原约束为何未兑现。
边界编码错误在边界处统一转域，不让调用方各自兜底；未捕获异常看 `_CxxThrowException` 下的 throw 点。

## lldb 备选流程 (cdb 不可用时)

仅在需要活体复现且允许运行时使用。PowerShell 从仓库根进入目标 CWD，结束恢复目录：

```powershell
Push-Location build/bin
try {
  python ../../.agents/skills/win-crash-debug/scripts/lldb_stack.py --lldb "<实际lldb.exe>" --exe ./AternyxMain.exe --args "-p ../../Test/Projects/General --frames 30" --log ../../temp/crash_lldb.log
} finally {
  Pop-Location
}
```

`lldb_stack.py` 的 `--cwd` 只声明未消费，目标继承调用方 CWD；不能以传该参数代替切目录。
`--timeout` 仅在 stdout 读取循环结束后的 wait 生效，不是整体运行时限；调用方必须施加外层总超时，
超时后确认调试器和目标进程树均已回收。脚本退出 2=崩溃、0=正常、1=环境错误。


- 退出 crash 复现手法: `--frames N` 让主循环自然跑完, 走 `~Engine → Shutdown` 完整退出路径; 没有帧数限制的程序 (如 editor) 用 `--close-after 30` (WM_CLOSE 等价用户点 X)
- `--bt-all` 抓所有线程; `--cmd "b xxx"` 预下断点, `--crash-cmd` 崩溃停止后追加检查；`--timeout` 缺省 300s 但受上述读取循环限制
- 输出结论: `stop reason = Exception 0xc0000005 ...` + `bt` 符号栈; 完整日志落 `temp/<exe>.lldb_stack.log`

## 自检 (怀疑链路坏了才做)

```bash
cd temp && cl /nologo /Zi /Od ../.agents/skills/win-crash-debug/scripts/mini_crash.c /Fe:mini_crash.exe \
  && "/c/Program Files (x86)/Windows Kits/10/Debuggers/x64/cdb.exe" -lines -c "g; k; q" mini_crash.exe
```

预期停在 `mini_crash.exe!deeper` 的 0xc0000005 并打印三层栈。注意 mini_crash.c 不能写中文注释: cl 默认按代码页 936 读源码, UTF-8 中文触发 C4819 吞换行导致编译错。

## 坑清单

- **"字段错位式"崩溃先怀疑陈旧 obj, 不是代码**: 症状——结构体前段字段完好、某偏移之后全是垃圾 (0xCC/野指针), 两个 TU 对同一头文件结构体布局理解不一致。跑 `python .agents/skills/project-workflow/scripts/ninja_deps_guard.py` 体检 (`--fix` 自愈): `#deps 0` = 头依赖记录为空, 该 obj 永不因头文件变化重编 (touch 头文件也无效)。注意 `#deps 0` 有良性与可疑两类, 详见 project-workflow 的 references/troubleshooting.md。实际案例: gateway 迭代给 `EngineCreateInfo` 插入字段后, 旧 obj 的 AternyxEditorMain 启动即 AV (`Engine::Initialize` 读 `engineRoot` 错位)。
- 构建"成功"不等于重编: `cmake --build` 只做 Linking 而目标行为异常时, 先查 obj 时间戳 vs 头文件时间戳, 再跑 deps 体检。
- **abort 弹窗 (R6010) 是 Debug CRT 在 `abort()` 入口后弹的 MessageBox**: 调试器下必须用 `bp ucrtbased!_wassert; bp ucrtbased!abort` 在弹窗前拦 (实测 /MDd 有效); 断在 `abort` 时栈顶是 `terminate → FindHandler` 说明是未捕获 C++ 异常, 真实 throw 点在 `_CxxThrowException` 下面两三帧。CRT 链接形态先 `dumpbin /dependents <exe> | grep -i crt` 确认 (ucrtbased = /MDd)。
- **cdb 输出量**: 引擎日志会刷屏, 一律重定向落文件再 grep; 外部脚本驱动编辑器时同理必须持续排水 stdout, 否则管道写满阻塞引擎 (高频假故障, 详见 `docs/modules/gateway/overview.md` 端到端验证手册)。
- **lldb 表达式报 "does not have a complete definition" 洪流是 PDB 噪音**: lldb 表达式求值要从 PDB 构建 Clang AST, 第三方库 (assimp/pugi/yaml-cpp/rapidjson 等) 的不完整类型触发成片报错。规避: 取值优先 `frame variable`; 表达式只做简单成员访问; 报错通常一次性、非致命。**这正是优先用 cdb 的原因** (原生 PDB 消费, 免疫该问题)。
- **`F:\DevKit` 与 `LLVM\bin` 是管理员权限目录**, agent 写不进; LLVM 重装/升级后若又缺 `python310.dll`, 请用户从 `~/.local/lib/lldb-py310/` 拷入, 或依赖脚本注入 PATH 兜底。lldb 报 `error while loading shared libraries` = 该 DLL 没找到, 不是 lldb 坏了。
- 残留验证实例会锁 exe 或占端口；按本会话记录的 PID 回收自起目标，不能按进程名杀掉用户实例。调试器超时退出后也确认目标已回收。
- 与 VS Code `cppvsdbg` 的关系: cppvsdbg 的引擎就是 cpptools 扩展里的 vsdbg, 但它只暴露 DAP (JSON) 协议, 命令行无人值守抓栈用 cdb/lldb batch; 要在 VS Code 里人肉调试时再配 `cppvsdbg` launch。
- cdb 不可用的离线备选：lldb `target create -c <dump> <exe>`。
