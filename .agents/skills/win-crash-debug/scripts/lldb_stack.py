#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lldb batch 模式崩溃抓栈驱动。

设计意图: 用 lldb 的 batch 模式 (-b) 把"启动目标 -> 等崩溃 -> 抓调用栈"固化成一条
无人值守命令, 供 agent 排查 crash 时直接使用。lldb 停在异常 (0xC0000005 等) 后自动
执行 -k 里的 backtrace 再退出, 输出即"崩溃现场 + 全符号栈"。

用法:
  python lldb_stack.py --exe build/bin/AternyxMain.exe --args "-p ../../Test/Projects/General --frames 30" \
      --cwd build/bin
退出码: 0=正常退出未崩溃; 2=捕获到崩溃(栈已打印); 1=环境/超时错误。
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

# 本机固定路径 (与 renderdoc-debug 等 skill 同一套约定): LLVM 安装于 F:/DevKit/LLVM
DEFAULT_LLDB = r"F:/DevKit/LLVM/bin/lldb.exe"
# LLVM 21 的 liblldb.dll 硬依赖 python310.dll (官方发行包未带, 本机 Python 是 3.11/3.12),
# 补装的 3.10 embeddable 运行时放这里; Windows 会沿 PATH 搜索 DLL, 调 lldb 前注入即可
DEFAULT_PY_DLL_DIR = os.path.join(os.path.expanduser("~"), ".local", "lib", "lldb-py310")

CRASH_RE = re.compile(r"stop reason = (.+)")
EXIT_RE = re.compile(r"Process \d+ exited with status = (-?\d+)")
LAUNCH_RE = re.compile(r"Process (\d+) launched:")


def find_lldb(explicit: str) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"[lldb_stack] 指定的 lldb 不存在: {p}")
        return p
    for candidate in (
        os.environ.get("ATERNYX_LLDB"),
        DEFAULT_LLDB,
    ):
        if candidate and Path(candidate).exists():
            return Path(candidate)
    raise SystemExit("[lldb_stack] 找不到 lldb.exe, 用 --lldb 或环境变量 ATERNYX_LLDB 指定")


def ensure_python_dll(lldb_path: Path, py_dll_dir: str) -> None:
    """liblldb.dll 缺 python310.dll 时把补装目录注入子进程 PATH。"""
    if (lldb_path.parent / "python310.dll").exists():
        return
    if not Path(py_dll_dir, "python310.dll").exists():
        raise SystemExit(
            f"[lldb_stack] lldb 缺 python310.dll 且补装目录无效: {py_dll_dir}\n"
            "按 skill 文档下载 python-3.10.11-embed-amd64.zip 解压到该目录"
        )
    os.environ["PATH"] = py_dll_dir + os.pathsep + os.environ.get("PATH", "")


def build_lldb_command(lldb: Path, exe: str, args: str, bt_all: bool, extra_cmds, crash_cmds) -> list:
    cmds = [f'target create "{exe}"']
    if args:
        run_args = " ".join(shlex.split(args.replace("\\", "/")))
        cmds.append(f"settings set -- target.run-args {run_args}")
    cmds.extend(extra_cmds)
    cmds.append("run")
    # -k = 崩溃停止时执行; 先全量栈, 再追加检查命令, 最后退出。clean-exit 时 -k 不触发
    on_crash = ["bt all" if bt_all else "bt", *crash_cmds, "quit"]
    argv = [str(lldb), "-b"]
    for c in cmds:
        argv += ["-o", c]
    for c in on_crash:
        argv += ["-k", c]
    return argv


def schedule_window_close(pid: int, delay_secs: int) -> None:
    """延时向被测进程主窗口发 WM_CLOSE: 等价用户点关闭按钮 (GLFW 置 shouldClose),
    驱动程序走完整退出路径; 与强杀不同, 不会跳过 Shutdown。"""
    def close() -> None:
        time.sleep(delay_secs)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid}).CloseMainWindow() | Out-Null"],
            capture_output=True)
        print(f"[lldb_stack] 已向 pid={pid} 主窗口发送 WM_CLOSE")
    threading.Thread(target=close, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="lldb batch 崩溃抓栈")
    parser.add_argument("--exe", required=True, help="被测可执行文件 (绝对/相对路径)")
    parser.add_argument("--args", default="", help='传给被测程序的参数串, 如 "-p ../../Test/Projects/General --frames 30" (被测程序 CWD=build/bin 时)')
    parser.add_argument("--cwd", default=None, help="被测程序工作目录 (缺省用 lldb 进程的 CWD)")
    parser.add_argument("--timeout", type=int, default=300, help="整体超时秒数 (缺省 300)")
    parser.add_argument("--bt-all", action="store_true", help="崩溃时打印所有线程的栈 (bt all)")
    parser.add_argument("--lldb", default=None, help="lldb.exe 路径 (缺省 F:/DevKit/LLVM/bin/lldb.exe)")
    parser.add_argument("--py-dll-dir", default=DEFAULT_PY_DLL_DIR, help="python310.dll 补装目录")
    parser.add_argument("--cmd", action="append", default=[], help="run 之前附加的 lldb 命令 (可重复, 如下断点)")
    parser.add_argument("--close-after", type=int, default=0, metavar="SECS",
                        help="被测进程启动 SECS 秒后向其主窗口发 WM_CLOSE (模拟用户关窗, 驱动"
                             "'运行到关窗才退出' 的程序走完整退出路径; 需被测进程有可见窗口)")
    parser.add_argument("--crash-cmd", action="append", default=[], metavar="CMD",
                        help="崩溃停止时在 bt 之后追加执行的 lldb 命令 (可重复, 如检查变量); 总是先 bt 再执行这些再退出")
    parser.add_argument("--log", default=None, help="完整 lldb 输出落盘路径 (缺省 <exe>.lldb_stack.log 于 CWD)")
    cli = parser.parse_args()

    exe = str(Path(cli.exe).resolve())
    if not Path(exe).exists():
        raise SystemExit(f"[lldb_stack] 目标不存在: {exe}")
    lldb = find_lldb(cli.lldb)
    ensure_python_dll(lldb, cli.py_dll_dir)
    log_path = Path(cli.log) if cli.log else None
    if log_path is None:
        # 仓库根的 temp/ 优先 (gitignore), 不在仓库内时落 CWD
        log_dir = Path("temp") if Path("temp").is_dir() else Path(".")
        log_path = log_dir / f"{Path(exe).stem}.lldb_stack.log"
    log_path = log_path.resolve()

    argv = build_lldb_command(lldb, exe, cli.args, cli.bt_all, cli.cmd, cli.crash_cmd)
    print(f"[lldb_stack] {' '.join(argv)}")
    print(f"[lldb_stack] cwd={os.getcwd()} timeout={cli.timeout}s")

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    lines: list = []
    close_scheduled = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            lines.append(line.rstrip("\n"))
            if cli.close_after > 0 and not close_scheduled:
                launch = LAUNCH_RE.search(line)
                if launch:
                    close_scheduled = True
                    schedule_window_close(int(launch.group(1)), cli.close_after)
        rc = proc.wait(timeout=cli.timeout)
    except subprocess.TimeoutExpired:
        # lldb 与被调试进程同进程树, /T 连带杀掉, 避免残留 exe 锁住 PDB/输出
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        print(f"[lldb_stack] 超时({cli.timeout}s), 已终止 lldb 进程树")
        return 1

    log_path.write_text("\n".join(lines), encoding="utf-8")
    full = "\n".join(lines)
    crashes = [m.group(1) for m in CRASH_RE.finditer(full)]
    exits = EXIT_RE.findall(full)
    exit_code = int(exits[-1]) if exits else (proc.returncode if rc else 0)

    print("\n==== lldb_stack 结论 ====")
    if crashes:
        print(f"崩溃: {crashes[-1]}")
        print(f"完整日志: {log_path}")
        return 2
    print(f"未捕获到崩溃; 进程退出码 {exit_code}")
    print(f"完整日志: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
