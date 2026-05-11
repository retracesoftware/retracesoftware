import fakeredis


def main():
    print("=== redis_fakeredis_test ===", flush=True)
    client = fakeredis.FakeRedis(decode_responses=True)
    client.hset("user:1", mapping={"name": "Ada", "role": "engineer"})
    client.lpush("jobs", "build", "test")
    client.incrby("counter", 3)

    user = client.hgetall("user:1")
    jobs = [client.rpop("jobs"), client.rpop("jobs")]
    counter = client.get("counter")

    assert user == {"name": "Ada", "role": "engineer"}
    assert jobs == ["build", "test"]
    assert counter == "3"
    print(f"user={user}", flush=True)
    print(f"jobs={jobs}", flush=True)
    print("redis fakeredis ok", flush=True)


if __name__ == "__main__":
    main()
