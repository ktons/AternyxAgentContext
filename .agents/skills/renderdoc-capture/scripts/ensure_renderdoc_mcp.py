"""Ensure a dcc-mcp renderdoc adapter instance is running.

stdio MCP 入口 (renderdoc_mcp_stdio.cmd) 在拉起 mcp-proxy 前先跑本脚本:
探测本机 FileRegistry 里是否已有可用的 renderdoc 实例, 没有则以分离进程启动
`python -m dcc_mcp_renderdoc` (adapter 内嵌 streamable-HTTP server, 启动时自动
注册到 9765 网关), 轮询等待其实例可用。

所有输出走 stderr: 本脚本之后 stdio 交给 mcp-proxy, stdout 属于 MCP 协议通道。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PYTHON = Path(sys.executable)
CLI = PYTHON.parent / "Scripts" / "dcc-mcp-cli.exe"
SERVER_LOG = Path(os.environ.get("TEMP", str(Path.home()))) / "dcc-mcp-renderdoc-adapter.log"
WAIT_SECS = 30


def log(msg: str) -> None:
    print(f"[ensure renderdoc-mcp] {msg}", file=sys.stderr, flush=True)


def list_renderdoc_instances() -> list[dict]:
    try:
        raw = subprocess.run(
            [str(CLI), "list", "--no-update-maintenance"],
            capture_output=True, text=True, timeout=30, encoding="utf-8",
        ).stdout
        payload = json.loads(raw.splitlines()[0])
    except (OSError, ValueError, subprocess.SubprocessError, IndexError) as exc:
        # CLI 不可达视为"无实例", 由启动分支兜底
        log(f"warning: dcc-mcp-cli list failed: {exc}")
        return []
    instances = payload.get("instances") or []
    return [i for i in instances if i.get("dcc_type") == "renderdoc"
            and i.get("status") == "available"]


def start_adapter() -> None:
    env = os.environ.copy()
    env.setdefault("DCC_MCP_RENDERDOC_CMD", r"C:\Program Files\RenderDoc\renderdoccmd.exe")
    env.setdefault("DCC_MCP_RENDERDOC_AUTO_DOWNLOAD", "0")
    log_file = open(SERVER_LOG, "ab")
    # DETACHED_PROCESS: adapter 生命周期独立于本 stdio 会话, 客户端断开不回收
    subprocess.Popen(
        [str(PYTHON), "-m", "dcc_mcp_renderdoc"],
        stdout=log_file, stderr=subprocess.STDOUT,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        env=env, cwd=str(PYTHON.parent.parent),
        close_fds=True,
    )
    log(f"adapter launching detached, log: {SERVER_LOG}")


def main() -> int:
    instances = list_renderdoc_instances()
    if not instances:
        start_adapter()
    deadline = time.monotonic() + WAIT_SECS
    while time.monotonic() < deadline:
        instances = list_renderdoc_instances()
        if instances:
            inst = instances[0]
            log(f"ready: instance {inst.get('instance_short')} at {inst.get('mcp_url')}")
            return 0
        time.sleep(1)
    log(f"error: no renderdoc adapter instance after {WAIT_SECS}s; "
        f"check {SERVER_LOG} and 'dcc-mcp-cli list'")
    return 0  # 不阻塞: 让 mcp-proxy 继续尝试, 网关/实例稍后恢复即可自愈


if __name__ == "__main__":
    sys.exit(main())
