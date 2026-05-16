"""DuckDB MCP server — stock.duckdb 전용, SELECT 전용."""
import asyncio
import json
from pathlib import Path

import duckdb
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

DB_PATH = Path(__file__).parent.parent / "data" / "stock.duckdb"

server = Server("stock-db")


def _conn():
    return duckdb.connect(str(DB_PATH), read_only=True)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="read_query",
            description="stock.duckdb에 SELECT 쿼리 실행. 쓰기 쿼리(INSERT/UPDATE/DELETE/DROP 등)는 거부됨.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "실행할 SELECT SQL"}
                },
                "required": ["sql"],
            },
        ),
        types.Tool(
            name="list_tables",
            description="DB의 모든 테이블 이름과 컬럼 목록 반환.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="describe_table",
            description="특정 테이블의 컬럼명/타입/NULL여부 반환.",
            inputSchema={
                "type": "object",
                "properties": {
                    "table": {"type": "string", "description": "테이블 이름"}
                },
                "required": ["table"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "read_query":
        sql = arguments["sql"].strip()
        # 쓰기 쿼리 차단 (간단한 키워드 체크)
        first_word = sql.split()[0].upper() if sql else ""
        if first_word not in ("SELECT", "WITH", "SHOW", "DESCRIBE", "PRAGMA"):
            return [types.TextContent(type="text", text="ERROR: SELECT 전용 서버입니다. 쓰기 쿼리는 허용되지 않습니다.")]
        try:
            conn = _conn()
            result = conn.execute(sql).fetchdf()
            conn.close()
            return [types.TextContent(type="text", text=result.to_string(index=False, max_rows=200))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"ERROR: {e}")]

    elif name == "list_tables":
        try:
            conn = _conn()
            tables = conn.execute("SHOW TABLES").fetchdf()
            out = []
            for table in tables["name"].tolist():
                cols = conn.execute(f"DESCRIBE {table}").fetchdf()
                col_names = ", ".join(cols["column_name"].tolist())
                out.append(f"{table}: {col_names}")
            conn.close()
            return [types.TextContent(type="text", text="\n".join(out))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"ERROR: {e}")]

    elif name == "describe_table":
        table = arguments["table"]
        try:
            conn = _conn()
            result = conn.execute(f"DESCRIBE {table}").fetchdf()
            conn.close()
            return [types.TextContent(type="text", text=result.to_string(index=False))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"ERROR: {e}")]

    return [types.TextContent(type="text", text=f"ERROR: 알 수 없는 tool: {name}")]


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
