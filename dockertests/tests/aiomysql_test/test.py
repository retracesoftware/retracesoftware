import asyncio
import os

import aiomysql


CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "test_user"),
    "password": os.getenv("MYSQL_PASSWORD", "test"),
    "db": os.getenv("MYSQL_DATABASE", "test_db"),
    "autocommit": True,
}


async def connect():
    last_error = None
    for _ in range(40):
        try:
            return await aiomysql.connect(**CONFIG)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.25)
    raise RuntimeError(f"aiomysql could not connect: {last_error}") from last_error


async def main_async():
    print("=== aiomysql_test ===")
    conn = await connect()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP TABLE IF EXISTS retrace_aiomysql_items")
            await cursor.execute(
                """
                CREATE TABLE retrace_aiomysql_items (
                    id INTEGER PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(100) NOT NULL,
                    score INTEGER NOT NULL
                )
                """
            )
            await cursor.executemany(
                "INSERT INTO retrace_aiomysql_items(name, score) VALUES(%s, %s)",
                [("ada", 3), ("grace", 5), ("katherine", 8)],
            )
            await cursor.execute(
                "SELECT name, score FROM retrace_aiomysql_items ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert rows == (("ada", 3), ("grace", 5), ("katherine", 8))
            await cursor.execute("DROP TABLE retrace_aiomysql_items")
            print("aiomysql record/replay scenario ok")
    finally:
        conn.close()
        await conn.ensure_closed()


if __name__ == "__main__":
    asyncio.run(main_async())
