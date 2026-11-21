"""项目抓帧驱动模板（复制到项目脚本目录后实现 TODO 钩子）。

目标：把“构建目标 → 拼启动参数 → 拉起并等待就绪 → 触发一次 RenderDoc capture → 回收进程”
固定成项目自己的驱动；通用分析脚本留在 renderdoc-capture skill。

用法示例：
    python do_capture.py --frames 900 --out temp/capture/frame
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def log(message: str) -> None:
    print(f"[capture] {message}", flush=True)


def find_renderdoc_cmd() -> Path:
    """按环境变量、常见安装位置、PATH 的顺序发现 renderdoccmd。"""
    env = os.environ.get("DCC_MCP_RENDERDOC_CMD")
    if env and Path(env).is_file():
        return Path(env)
    candidates = [
        Path(r"C:\Program Files\RenderDoc\renderdoccmd.exe"),
        Path(r"C:\Program Files (x86)\RenderDoc\renderdoccmd.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("renderdoccmd")
    if found:
        return Path(found)
    raise SystemExit("renderdoccmd not found; set DCC_MCP_RENDERDOC_CMD")


def build_target(args: argparse.Namespace) -> Path:
    """TODO: 用项目构建系统构建目标，返回可执行文件绝对路径。"""
    raise NotImplementedError("build_target: 接入项目的 configure/build 命令")


def target_argv(exe: Path, args: argparse.Namespace) -> list[str]:
    """TODO: 拼目标启动参数，例如工程目录、场景、离屏/窗口、帧数。"""
    argv = [str(exe)]
    if args.project:
        argv += ["--project", str(Path(args.project).resolve())]
    if args.scene:
        argv += ["--scene", args.scene]
    argv += list(args.extra_args)
    return argv


def wait_until_ready(proc: subprocess.Popen, args: argparse.Namespace) -> None:
    """TODO: 等待目标进入可抓帧状态。

    可选信号：日志出现场景加载完成、窗口可查询、会话文件出现、固定预热帧数。
    加载阶段抓到的帧不作为问题证据；等待上限和超时诊断写进这里。
    """
    time.sleep(args.warmup_secs)
    if proc.poll() is not None:
        raise SystemExit(f"target exited before capture: {proc.returncode}")


def trigger_capture(proc: subprocess.Popen, out_path: Path, args: argparse.Namespace) -> None:
    """TODO: 通过 dcc-mcp runtime、Target Control 或项目抓帧接口触发一次 capture。

    推荐在目标创建图形设备前完成注入；已建设备的进程通常无法补挂。
    """
    raise NotImplementedError("trigger_capture: 接入项目的抓帧接口")


def stop_target(proc: subprocess.Popen, keep_alive: bool) -> None:
    """只回收本驱动拉起的进程：先优雅退出，超时再强制结束。"""
    if keep_alive or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=0, help="触发前预热帧数")
    parser.add_argument("--warmup-secs", type=float, default=3.0)
    parser.add_argument("--out", type=Path, required=True, help="capture 输出前缀")
    parser.add_argument("--project", help="项目/资产目录")
    parser.add_argument("--scene", help="场景相对路径")
    parser.add_argument("--keep-alive", action="store_true")
    parser.add_argument("extra_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    exe = build_target(args)
    argv = target_argv(exe, args)
    log(f"target: {argv[0]}")
    proc = subprocess.Popen(argv, cwd=exe.parent)
    try:
        wait_until_ready(proc, args)
        trigger_capture(proc, args.out, args)
        log(f"capture: {args.out}")
    finally:
        stop_target(proc, args.keep_alive)
    return 0


if __name__ == "__main__":
    sys.exit(main())
