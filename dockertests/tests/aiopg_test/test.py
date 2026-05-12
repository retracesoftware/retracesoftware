import asyncio
import os

import aiopg


DSN = (
    f"dbname={os.getenv('DB_NAME', 'test_db')} "
    f"user={os.getenv('DB_USER', 'test_user')} "
    f"password={os.getenv('DB_PASSWORD', 'test')} "
    f"host={os.getenv('DB_HOST', 'localhost')} "
    f"port={os.getenv('DB_PORT', '5432')}"
)


async def connect():
    last_error = None
    for _ in range(30):
        try:
            return await aiopg.connect(DSN)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.25)
    raise RuntimeError(f"aiopg could not connect: {last_error}") from last_error


async def main_async():
    print("=== aiopg_test ===")
    conn = await connect()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP TABLE IF EXISTS retrace_aiopg_items")
            await cursor.execute(
                """
                CREATE TABLE retrace_aiopg_items (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    score INTEGER NOT NULL
                )
                """
            )
            await cursor.execute(
                """
                INSERT INTO retrace_aiopg_items(name, score)
                VALUES (%s, %s), (%s, %s), (%s, %s)
                """,
                ("ada", 3, "grace", 5, "katherine", 8),
            )

            await cursor.execute(
                "SELECT name, score FROM retrace_aiopg_items ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert rows == [("ada", 3), ("grace", 5), ("katherine", 8)]

            await cursor.execute("DROP TABLE retrace_aiopg_items")
            print("aiopg record/replay scenario ok")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main_async())
