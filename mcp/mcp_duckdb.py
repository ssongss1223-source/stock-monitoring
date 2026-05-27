"""DuckDB MCP server — stock.duckdb 전용, SELECT 전용."""
import asyncio
import threading
from pathlib import Path

import duckdb
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

DB_PATH = Path.home() / "stock_monitor_data" / "stock.duckdb"
_TIMEOUT = 30.0

server = Server("stock-db")
_local = threading.local()  # 스레드별 독립 연결 (thread-safe)


def _get_conn() -> duckdb.DuckDBPyConnection:
    import time, sys
    if not hasattr(_local, "conn") or _local.conn is None:
        t = time.time()
        print(f"[mcp_duckdb] connecting to {DB_PATH}", file=sys.stderr, flush=True)
        _local.conn = duckdb.connect(str(DB_PATH), read_only=True)
        print(f"[mcp_duckdb] connected in {time.time()-t:.3f}s", file=sys.stderr, flush=True)
    return _local.conn


def _query(sql: str) -> str:
    import time, sys
    print(f"[mcp_duckdb] _query start: {sql[:60]}", file=sys.stderr, flush=True)
    t = time.time()
    try:
        result = _get_conn().execute(sql).fetchdf().to_string(index=False, max_rows=200)
        print(f"[mcp_duckdb] _query done in {time.time()-t:.3f}s", file=sys.stderr, flush=True)
        return result
    except Exception as e:
        print(f"[mcp_duckdb] _query error: {e}", file=sys.stderr, flush=True)
        # 연결이 깨진 경우 재연결 1회 시도
        _local.conn = None
        return _get_conn().execute(sql).fetchdf().to_string(index=False, max_rows=200)


def _list_tables() -> str:
    tables = _get_conn().execute("SHOW TABLES").fetchdf()["name"].tolist()
    out = []
    for t in tables:
        cols = _get_conn().execute(f"DESCRIBE {t}").fetchdf()
        out.append(f"{t}: {', '.join(cols['column_name'].tolist())}")
    return "\n".join(out)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="read_query",
            description="stock.duckdb에 SELECT 쿼리 실행. 쓰기 쿼리(INSERT/UPDATE/DELETE/DROP 등)는 거부됨.",
            inputSchema={
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "실행할 SELECT SQL"}},
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
                "properties": {"table": {"type": "string", "description": "테이블 이름"}},
                "required": ["table"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    def respond(text: str) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=text)]

    loop = asyncio.get_running_loop()

    if name == "read_query":
        sql = arguments["sql"].strip()
        first_word = sql.split()[0].upper() if sql else ""
        if first_word not in ("SELECT", "WITH", "SHOW", "DESCRIBE", "PRAGMA"):
            return respond("ERROR: SELECT 전용 서버입니다.")
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _query, sql),
                timeout=_TIMEOUT,
            )
            return respond(result)
        except asyncio.TimeoutError:
            return respond(f"ERROR: 쿼리 타임아웃 ({_TIMEOUT}s) — DB 파일 잠금 확인 필요")
        except Exception as e:
            return respond(f"ERROR: {e}")

    elif name == "list_tables":
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _list_tables),
                timeout=_TIMEOUT,
            )
            return respond(result)
        except asyncio.TimeoutError:
            return respond(f"ERROR: 타임아웃 ({_TIMEOUT}s)")
        except Exception as e:
            return respond(f"ERROR: {e}")

    elif name == "describe_table":
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _query, f"DESCRIBE {arguments['table']}"),
                timeout=_TIMEOUT,
            )
            return respond(result)
        except asyncio.TimeoutError:
            return respond(f"ERROR: 타임아웃 ({_TIMEOUT}s)")
        except Exception as e:
            return respond(f"ERROR: {e}")

    return respond(f"ERROR: 알 수 없는 tool: {name}")


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
