"""VM SSH MCP server — GCP VM 명령 실행 전용."""
import asyncio
import subprocess
from pathlib import Path

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

VM_INSTANCE = "instance-20260505-092414"
VM_ZONE = "us-central1-a"
VM_APP_DIR = "/opt/stock-monitor"
GCLOUD_CMD = r"C:\Users\KHSong\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

server = Server("vm-ssh")


def _run_gcloud_ssh(command: str, timeout: int = 60) -> str:
    cmd = (
        f'"{GCLOUD_CMD}" compute ssh {VM_INSTANCE}'
        f' --zone={VM_ZONE} --quiet'
        f' "--ssh-flag=-o BatchMode=yes"'
        f' "--ssh-flag=-o ConnectTimeout=20"'
        f' --command="{command}"'
    )
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        out = result.stdout.decode("utf-8", errors="replace").strip()
        err = result.stderr.decode("utf-8", errors="replace").strip()
        if result.returncode != 0 and not out:
            return f"ERROR (exit {result.returncode}): {err}"
        return out or err
    except subprocess.TimeoutExpired:
        return f"ERROR: 타임아웃 ({timeout}s 초과)"


async def _gcloud_ssh(command: str, timeout: int = 60) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_gcloud_ssh, command, timeout)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="ssh_run",
            description="VM에서 shell 명령 실행. 결과(stdout/stderr) 반환.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "실행할 shell 명령"},
                    "timeout": {"type": "integer", "description": "타임아웃 (초, 기본 60)", "default": 60},
                },
                "required": ["command"],
            },
        ),
        types.Tool(
            name="tail_log",
            description="VM 로그 파일 tail. 기본 경로는 /tmp/run_now.log 또는 stock_monitor.log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "로그 파일 경로 (기본: /tmp/run_now.log)", "default": "/tmp/run_now.log"},
                    "lines": {"type": "integer", "description": "출력할 줄 수 (기본 50)", "default": 50},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="service_status",
            description="stock-monitor 서비스 상태 + 최근 로그 20줄 반환.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="service_restart",
            description="stock-monitor 서비스 재시작.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "ssh_run":
        command = arguments["command"]
        timeout = int(arguments.get("timeout", 60))
        result = await _gcloud_ssh(command, timeout=timeout)
        return [types.TextContent(type="text", text=result)]

    elif name == "tail_log":
        path = arguments.get("path", "/tmp/run_now.log")
        lines = int(arguments.get("lines", 50))
        result = await _gcloud_ssh(f"tail -{lines} {path}", timeout=60)
        return [types.TextContent(type="text", text=result)]

    elif name == "service_status":
        result = await _gcloud_ssh(
            "systemctl is-active stock-monitor && echo '---' && "
            f"journalctl -u stock-monitor -n 20 --no-pager 2>/dev/null || "
            f"tail -20 {VM_APP_DIR}/stock_monitor.log 2>/dev/null",
            timeout=60,
        )
        return [types.TextContent(type="text", text=result)]

    elif name == "service_restart":
        result = await _gcloud_ssh(
            "sudo systemctl restart stock-monitor && sleep 2 && systemctl is-active stock-monitor",
            timeout=60,
        )
        return [types.TextContent(type="text", text=result)]

    return [types.TextContent(type="text", text=f"ERROR: 알 수 없는 tool: {name}")]


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
