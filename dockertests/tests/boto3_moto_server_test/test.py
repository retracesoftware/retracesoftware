import os
import time
from urllib.error import URLError
from urllib.request import urlopen

import boto3
from botocore.config import Config


ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL", "http://127.0.0.1:15000")


def wait_for_moto():
    last_error = None
    for _ in range(120):
        try:
            with urlopen(f"{ENDPOINT_URL}/moto-api/", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"moto server did not become ready: {last_error}") from last_error


def main():
    print("=== boto3_moto_server_test ===")
    wait_for_moto()

    s3 = boto3.client(
        "s3",
        endpoint_url=ENDPOINT_URL,
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(
            retries={"max_attempts": 1, "mode": "standard"},
            s3={"addressing_style": "path"},
            user_agent="retrace-boto3-moto-test",
        ),
    )

    bucket = "retrace-live-boto3-demo"
    key = "invoices/001.txt"
    body = b"invoice-total=42"

    s3.create_bucket(Bucket=bucket)
    s3.put_object(Bucket=bucket, Key=key, Body=body, Metadata={"source": "dockertest"})

    response = s3.get_object(Bucket=bucket, Key=key)
    assert response["Body"].read() == body
    assert response["Metadata"] == {"source": "dockertest"}

    listed = s3.list_objects_v2(Bucket=bucket, Prefix="invoices/")
    assert [item["Key"] for item in listed["Contents"]] == [key]

    s3.delete_object(Bucket=bucket, Key=key)
    s3.delete_bucket(Bucket=bucket)
    print("boto3 moto server record/replay scenario ok")


if __name__ == "__main__":
    main()
