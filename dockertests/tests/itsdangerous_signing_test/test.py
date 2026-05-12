from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer


def main():
    print("=== itsdangerous_signing_test ===")
    serializer = URLSafeSerializer("retrace-secret", salt="dockertest")
    token = serializer.dumps({"user": "ada", "scope": ["read", "debug"]})
    payload = serializer.loads(token)
    print(f"signed {payload['user']} scopes={','.join(payload['scope'])}")
    assert payload == {"user": "ada", "scope": ["read", "debug"]}

    timed = URLSafeTimedSerializer("retrace-secret", salt="timed")
    timed_token = timed.dumps({"request_id": 17})
    timed_payload = timed.loads(timed_token, max_age=60)
    print(f"timed request_id={timed_payload['request_id']}")
    assert timed_payload == {"request_id": 17}
    print("itsdangerous signing ok")


if __name__ == "__main__":
    main()
