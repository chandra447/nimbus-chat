from __future__ import annotations

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.settings import Settings


async def create_sqlite_checkpointer(
    settings: Settings,
) -> tuple[aiosqlite.Connection, AsyncSqliteSaver]:
    connection = await aiosqlite.connect(settings.langgraph_sqlite_conn_string)
    saver = AsyncSqliteSaver(connection)
    await saver.setup()
    return connection, saver
