"""VM SSH MCP server — paramiko 직접 SSH."""
import asyncio
from pathlib import Path

import paramiko
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

VM_HOST = "34.171.35.91"
VM_USER = "KHSong"
VM_APP_DIR = "/opt/stock-monitor"
SSH_KEY = str(Path.home() / ".ssh" / "google_compute_engine")

server = Server("vm-ssh")


def _ssh_run(command: str, timeout: int = 60) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            VM_HOST,
            username=VM_USER,
            key_filename=SSH_KEY,
            timeout=10,
            banner_timeout=10,
        )
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0 and not out:
            return f"ERROR (exit {exit_code}): {err}"
        return out or err
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        client.close()


async def _run(command: str, timeout: int = 60) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _ssh_run, command, timeout)


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
            description="VM 로그 파일 tail. 기본: /tmp/run_now.log",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "로그 파일 경로", "default": "/tmp/run_now.log"},
                    "lines": {"type": "integer", "description": "출력 줄 수 (기본 50)", "default": 50},
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
        result = await _run(arguments["command"], timeout=int(arguments.get("timeout", 60)))

    elif name == "tail_log":
        path = arguments.get("path", "/tmp/run_now.log")
        lines = int(arguments.get("lines", 50))
        result = await _run(f"tail -{lines} {path}")

    elif name == "service_status":
        result = await _run(
            "systemctl is-active stock-monitor && echo '---' && "
            f"journalctl -u stock-monitor -n 20 --no-pager 2>/dev/null || "
            f"tail -20 {VM_APP_DIR}/stock_monitor.log 2>/dev/null"
        )

    elif name == "service_restart":
        result = await _run(
            "sudo systemctl restart stock-monitor && sleep 2 && systemctl is-active stock-monitor"
        )

    else:
        result = f"ERROR: 알 수 없는 tool: {name}"

    return [types.TextContent(type="text", text=result)]


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
