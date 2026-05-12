import asyncio
import os

import asyncpg


CONFIG = {
    "database": os.getenv("DB_NAME", "test_db"),
    "user": os.getenv("DB_USER", "test_user"),
    "password": os.getenv("DB_PASSWORD", "test"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
}


async def connect():
    last_error = None
    for _ in range(30):
        try:
            return await asyncpg.connect(**CONFIG)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.25)
    raise RuntimeError(f"asyncpg could not connect: {last_error}") from last_error


async def main_async():
    print("=== asyncpg_test ===")
    conn = await connect()
    try:
        await conn.execute("DROP TABLE IF EXISTS retrace_asyncpg_items")
        await conn.execute(
            """
            CREATE TABLE retrace_asyncpg_items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                score INTEGER NOT NULL
            )
            """
        )
        await conn.executemany(
            "INSERT INTO retrace_asyncpg_items(name, score) VALUES($1, $2)",
            [("ada", 3), ("grace", 5), ("katherine", 8)],
        )
        rows = await conn.fetch(
            "SELECT name, score FROM retrace_asyncpg_items ORDER BY id"
        )
        assert [(row["name"], row["score"]) for row in rows] == [
            ("ada", 3),
            ("grace", 5),
            ("katherine", 8),
        ]

        tx = conn.transaction()
        await tx.start()
        await conn.execute(
            "INSERT INTO retrace_asyncpg_items(name, score) VALUES($1, $2)",
            "rollback",
            99,
        )
        await tx.rollback()
        count = await conn.fetchval("SELECT count(*) FROM retrace_asyncpg_items")
        assert count == 3

        await conn.execute("DROP TABLE retrace_asyncpg_items")
        print("asyncpg record/replay scenario ok")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main_async())
