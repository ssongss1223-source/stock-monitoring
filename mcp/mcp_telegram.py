"""Telegram MCP server — 텔레그램 메시지 발송."""
import asyncio
import os
from pathlib import Path

import requests
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

server = Server("telegram")


def _send(text: str, parse_mode: str = "HTML") -> str:
    if not BOT_TOKEN or not CHAT_ID:
        return "ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 미설정"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        if resp.ok:
            return f"OK (message_id={resp.json().get('result', {}).get('message_id')})"
        return f"ERROR {resp.status_code}: {resp.text}"
    except Exception as e:
        return f"ERROR: {e}"


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="send_message",
            description="텔레그램 채널에 메시지 발송. HTML 태그(<b>, <i>, <code> 등) 사용 가능.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "발송할 메시지 내용"},
                    "parse_mode": {
                        "type": "string",
                        "description": "파싱 모드: HTML(기본) 또는 Markdown",
                        "default": "HTML",
                    },
                },
                "required": ["text"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "send_message":
        text = arguments["text"]
        parse_mode = arguments.get("parse_mode", "HTML")
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _send, text, parse_mode)
        return [types.TextContent(type="text", text=result)]
    return [types.TextContent(type="text", text=f"ERROR: 알 수 없는 tool: {name}")]


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
