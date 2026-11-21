# Windows 崩溃操作参考

先发现本机工具：在 PowerShell 用 `Get-Command cdb,lldb -ErrorAction SilentlyContinue`，或查 Windows SDK
Debuggers/x64 与实际 LLVM 安装目录。记录实际发现的工具路径与版本；下面命令中的 `<...>` 按本次环境替换。
按 PID 回收本会话启动的目标，保留用户自行启动的实例。

## 现场与符号

现场目录通常为 `<project>/temp/crash/crash-<pid>-<unix秒>/`，meta 可定位日志、帧号、线程与内存水位；
离线取栈命令见 SKILL.md。macOS 的 stacktrace 报告头带 atos 命令，可直接使用。无现场或挂死时活体复现。

## 工具发现

| 工具 | 用法 |
|---|---|
| cdb（首选） | 查 Windows SDK 的 `Debuggers/x64/cdb.exe`；安装时勾选 “Debugging Tools for Windows”，或运行 `winsdksetup.exe /features OptionId.WindowsDesktopDebuggers /q /norestart`。VS Installer 的 “C++/CLI 支持” 组件服务于托管/本机混合开发，cdb 由 Windows SDK 提供 |
| lldb（备选） | 查实际 LLVM 安装目录的 `lldb.exe`，配合系统 Python 调 `scripts/lldb_stack.py`（仅标准库）；脚本退出码 2=崩溃、0=正常、1=环境错误 |
| 自检程序 | `scripts/mini_crash.c`：空指针解引用三层调用，用于验证抓栈链路本身可用 |

## 活体断点与窗口

被测进程 CWD 用子 shell 切换，输出重定向落日志：

```bash
(cd <运行目录> && "<实际cdb.exe>" \
  -lines -c "bp <断点>; g; .echo ===HIT===; k; q" \
  <target.exe> <参数>) > temp/crash_cdb.log 2>&1
```

- `-lines`：栈帧带源码行；`-c` 串在初始断点执行：下断点 → `g` → 命中任意断点或二次异常后执行 `k`（带符号栈）→ `q`。
- **按崩溃类型选断点**：
  - AV/硬异常：`g` 遇未处理异常自动停，`k` 即栈；也可用 `sxe 0xc0000005` 提升为首断。
  - abort/弹窗类（Debug Error! “abort() has been called”）：预下 `bp ucrtbased!_wassert; bp ucrtbased!abort; g; k; q`，
    抢在 CRT MessageBox 之前取栈；Release/MT 线程没有 ucrtbased 时下 `bp abort`。
  - 未捕获 C++ 异常 → terminate → abort：上述断点覆盖该路径；terminate 在栈展开前调用，`k` 中可见
    `_CxxThrowException → thrower` 的 throw 现场。
- **退出码判读**：正常运行不崩时 `g` 持续运行，用 `timeout N cdb ...` 兜底（exit=124 表示窗口期无崩溃，
  再结合日志确认无 `EH exception` 或断点命中）；崩溃时命令串执行完自行 `q` 退出。
- **交互式常驻目标**（如 editor）：复抓验证以“窗口期断点零命中 + 关键功能日志正常”为准。

`this=nullptr` 等栈帧先查调用链、重入和销毁顺序；修复时解释原约束为何未兑现。
边界编码错误在边界处统一转域，调用方按统一契约处理；未捕获异常在 `_CxxThrowException` 下找 throw 点。

## lldb 备选流程

需要活体复现且允许运行时使用。PowerShell 进入目标 CWD，结束恢复目录：

```powershell
Push-Location <运行目录>
try {
  python <workspace>/.agents/skills/win-crash-debug/scripts/lldb_stack.py --lldb "<实际lldb.exe>" --exe <target.exe> --args "<参数>" --log <workspace>/temp/crash_lldb.log
} finally {
  Pop-Location
}
```

`lldb_stack.py` 的 `--cwd` 只声明、不消费，目标继承调用方 CWD；切目录由调用方完成。
`--timeout` 在 stdout 读取循环结束后的 wait 生效，调用方同时施加外层总超时，超时后确认调试器和
目标进程树均已回收。脚本退出码 2=崩溃、0=正常、1=环境错误。

- 退出复现手法：`--frames N` 让主循环自然跑完，走完整 Shutdown 路径；没有帧数限制的程序用
  `--close-after 30`（WM_CLOSE 等价用户点关闭）。
- `--bt-all` 抓所有线程；`--cmd "b xxx"` 预下断点；`--crash-cmd` 在崩溃停止后追加检查；
  `--timeout` 缺省 300s，受上述读取循环限制。
- 结论输出：`stop reason = Exception 0xc0000005 ...` + `bt` 符号栈；完整日志落 `temp/<exe>.lldb_stack.log`。

## 自检

怀疑抓栈链路异常时执行：

```bash
cd temp && cl /nologo /Zi /Od <workspace>/.agents/skills/win-crash-debug/scripts/mini_crash.c /Fe:mini_crash.exe \
  && "<实际cdb.exe>" -lines -c "g; k; q" mini_crash.exe
```

预期停在 `mini_crash.exe!deeper` 的 0xc0000005 并打印三层栈。`mini_crash.c` 保持 ASCII 注释：
cl 默认按代码页 936 读源码，UTF-8 中文会触发 C4819。

## 坑清单

- **“字段错位式”崩溃优先查陈旧 obj**：症状是结构体前段字段完好、某偏移之后全是垃圾（0xCC/野指针），
  两个 TU 对同一头文件的布局理解不一致。用构建系统的头依赖记录或 obj/头文件时间戳核对；
  `#deps 0` 表示头依赖记录为空，该 obj 会一直跳过头文件变化后的重编。构建“成功”只做 Linking 而行为异常时，
  先查 obj 时间戳，再核对依赖记录。
- **abort 弹窗（R6010）是 Debug CRT 在 `abort()` 入口后弹出的 MessageBox**：调试器下用
  `bp ucrtbased!_wassert; bp ucrtbased!abort` 在弹窗前拦截。断在 `abort` 且栈顶为
  `terminate → FindHandler` 时，真实 throw 点在 `_CxxThrowException` 下面两三帧。先用
  `dumpbin /dependents <exe> | findstr /i crt` 确认 CRT 链接形态（ucrtbased = /MDd）。
- **cdb 输出量**：引擎日志会刷屏，统一重定向落文件再检索；外部脚本驱动编辑器时持续排水 stdout，
  保持管道畅通。
- **lldb 表达式报 “does not have a complete definition” 洪流是 PDB 噪音**：lldb 表达式求值要从 PDB
  构建 Clang AST，第三方库的不完整类型会触发成片报错。取值优先 `frame variable`，表达式只做简单成员访问；
  报错通常一次性、非致命。cdb 直接消费原生 PDB，在该场景下更稳。
- **lldb 依赖 DLL**：重装/升级 LLVM 后若缺 `python310.dll`，从 `~/.local/lib/lldb-py310/` 拷入，
  或由脚本注入 PATH。报 `error while loading shared libraries` 时按缺失 DLL 处理。
- **残留验证实例会锁 exe 或占端口**：按本会话记录的 PID 回收自起目标，保留用户实例；
  调试器超时退出后同样确认目标已回收。
- **与 VS Code `cppvsdbg` 的关系**：cppvsdbg 的引擎是 cpptools 扩展里的 vsdbg，只暴露 DAP（JSON）协议；
  命令行无人值守抓栈用 cdb/lldb batch，在 VS Code 里人工调试时再配 `cppvsdbg` launch。
- **cdb 不可用的离线备选**：lldb `target create -c <dump> <exe>`。
