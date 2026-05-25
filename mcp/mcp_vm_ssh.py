"""VM SSH MCP server — IAP TCP 터널 + paramiko (persistent connection)."""
import asyncio
import socket
import subprocess
import threading
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

# ── Persistent connection state ────────────────────────────────────────────────
_lock = threading.Lock()
_tunnel: subprocess.Popen | None = None
_client: paramiko.SSHClient | None = None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: int = 35) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _teardown() -> None:
    """기존 터널과 SSH 클라이언트를 정리."""
    global _tunnel, _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        _client = None
    if _tunnel is not None:
        _tunnel.terminate()
        try:
            _tunnel.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _tunnel.kill()
        _tunnel = None


def _is_alive() -> bool:
    """현재 SSH 연결이 살아있는지 확인. 채널 오픈 테스트까지 수행."""
    if _client is None:
        return False
    transport = _client.get_transport()
    if transport is None or not transport.is_active():
        return False
    try:
        transport.send_ignore()
        # send_ignore만으로는 exec_command 가능 여부를 보장하지 못함
        # 실제 세션 채널이 열리는지 짧게 테스트
        chan = transport.open_session(timeout=5)
        chan.close()
        return True
    except Exception:
        return False


def _connect() -> None:
    """새 IAP 터널 + SSH 연결 수립. 실패 시 예외 발생."""
    global _tunnel, _client
    _teardown()

    port = _free_port()
    _NO_WINDOW = 0x08000000
    _tunnel = subprocess.Popen(
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

    if not _wait_for_port(port):
        rc = _tunnel.poll()
        _teardown()
        raise RuntimeError(f"IAP tunnel startup timeout (35s), process exit={rc}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        "localhost", port=port,
        username=VM_USER, key_filename=SSH_KEY,
        timeout=10, banner_timeout=15,
    )
    # keepalive: 60초마다 패킷 전송 → GCP IAP idle timeout 방지
    client.get_transport().set_keepalive(60)
    # IAP 터널이 포트를 열었더라도 SSH 세션 채널이 안정화되기까지 짧게 대기
    time.sleep(1)
    _client = client


def _get_client() -> paramiko.SSHClient:
    """연결이 살아있으면 재사용, 끊겼으면 재연결."""
    if not _is_alive():
        _connect()
    return _client


def _ssh_run(command: str, timeout: int = 60) -> str:
    # 리다이렉트(> file)가 있으면 paramiko가 출력을 읽지 못함 → 사용 금지
    if " > " in command or " >> " in command:
        return (
            "ERROR: ssh_run에 출력 리다이렉트(> / >>)를 쓰면 paramiko가 출력을 읽지 못합니다. "
            "리다이렉트 없이 명령을 실행하세요. 백그라운드 실행이 필요하면 screen -dm을 사용하세요."
        )

    with _lock:
        for attempt in range(2):
            try:
                client = _get_client()
                _, stdout, stderr = client.exec_command(command, timeout=timeout)
                out = stdout.read().decode("utf-8", errors="replace").strip()
                err = stderr.read().decode("utf-8", errors="replace").strip()
                exit_code = stdout.channel.recv_exit_status()
                if exit_code != 0:
                    combined = "\n".join(filter(None, [out, err]))
                    return f"ERROR (exit {exit_code}): {combined or '(no output)'}"
                return out or err or "(completed with no output)"
            except Exception as e:
                if attempt == 0:
                    # 연결 끊김 → 강제 재연결 후 1회 재시도
                    _teardown()
                    time.sleep(5)  # IAP 터널 정리 + 재연결 안정화 대기
                else:
                    return f"ERROR: {e}"
    return "ERROR: unreachable"


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
