import os
import time

from redis.sentinel import Sentinel


SENTINEL_HOST = os.getenv("REDIS_SENTINEL_HOST", "127.0.0.1")
SENTINEL_PORT = int(os.getenv("REDIS_SENTINEL_PORT", "26379"))
MASTER_NAME = os.getenv("REDIS_SENTINEL_MASTER", "mymaster")
FORCE_MASTER_IP = os.getenv("REDIS_SENTINEL_FORCE_MASTER_IP")


def connect_sentinel():
    sentinel = Sentinel(
        [(SENTINEL_HOST, SENTINEL_PORT)],
        socket_timeout=2,
        decode_responses=True,
        force_master_ip=FORCE_MASTER_IP,
    )
    last_error = None
    for _ in range(120):
        try:
            sentinel.discover_master(MASTER_NAME)
            return sentinel
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"redis sentinel did not become ready: {last_error}") from last_error


def wait_for_master_change(sentinel, original_master):
    last_master = original_master
    for _ in range(60):
        try:
            current = sentinel.discover_master(MASTER_NAME)
            last_master = current
            if current != original_master:
                return current
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"sentinel did not fail over from {original_master}; last={last_master}")


def main():
    print("=== redis_sentinel_failover_test ===")
    sentinel = connect_sentinel()

    original_master = sentinel.discover_master(MASTER_NAME)
    master = sentinel.master_for(MASTER_NAME)
    master.set("retrace:sentinel", "before")
    assert master.get("retrace:sentinel") == "before"

    # Exercise Sentinel failover control flow. The recorded external calls
    # capture the exact Sentinel/Redis responses; replay must not touch Redis.
    sentinel.sentinel_failover(MASTER_NAME)
    new_master = wait_for_master_change(sentinel, original_master)
    assert new_master != original_master

    promoted = sentinel.master_for(MASTER_NAME)
    promoted.set("retrace:sentinel", "after")
    assert promoted.get("retrace:sentinel") == "after"
    print("redis sentinel failover record/replay scenario ok")


if __name__ == "__main__":
    main()
