"""VM SSH MCP server — IAP TCP 터널 + paramiko."""
import asyncio
import socket
import subprocess
import time
from pathlib import Path

# gcloud이 Claude Code 프로세스 PATH에 없는 경우를 대비해 직접 지정
_GCLOUD = r"C:\Users\KHSong\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

import paramiko
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

VM_INSTANCE = "instance-20260505-092414"
VM_ZONE = "us-central1-a"
VM_PROJECT = "stock-monitoring-495409"
VM_USER = "KHSong"
VM_APP_DIR = "/opt/stock-monitor"
SSH_KEY = str(Path.home() / ".ssh" / "google_compute_engine")

server = Server("vm-ssh")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: int = 30) -> bool:
    """포트가 열릴 때까지 폴링 (gcloud 출력 메시지에 의존하지 않음)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _ssh_run(command: str, timeout: int = 60) -> str:
    port = _free_port()

    _NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW — 콘솔 없는 VSCode 환경에서 필수
    tunnel = subprocess.Popen(
        [
            "cmd", "/c", _GCLOUD, "compute", "start-iap-tunnel",
            f"--project={VM_PROJECT}",
            f"--zone={VM_ZONE}",
            VM_INSTANCE, "22",
            f"--local-host-port=localhost:{port}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )

    try:
        if not _wait_for_port(port, timeout=35):
            rc = tunnel.poll()
            return f"ERROR: IAP tunnel startup timeout (35s), process exit={rc}"

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            "localhost", port=port,
            username=VM_USER, key_filename=SSH_KEY,
            timeout=10, banner_timeout=15,
        )
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0 and not out:
                return f"ERROR (exit {exit_code}): {err}"
            return out or err
        finally:
            client.close()
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=3)
        except subprocess.TimeoutExpired:
            tunnel.kill()


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
