import os
import time

import redis


def connect():
    client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
        socket_timeout=2,
    )
    last_error = None
    for _ in range(30):
        try:
            client.ping()
            return client
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"redis could not connect: {last_error}") from last_error


def main():
    print("=== redis_live_test ===")
    client = connect()
    client.flushdb()
    client.hset("retrace:user:1", mapping={"name": "Ada", "role": "engineer"})
    client.rpush("retrace:events", "created", "updated", "read")
    client.incrby("retrace:counter", 7)

    assert client.hgetall("retrace:user:1") == {"name": "Ada", "role": "engineer"}
    assert client.lrange("retrace:events", 0, -1) == ["created", "updated", "read"]
    assert client.get("retrace:counter") == "7"
    client.delete("retrace:user:1", "retrace:events", "retrace:counter")
    print("redis live server record/replay scenario ok")


if __name__ == "__main__":
    main()
