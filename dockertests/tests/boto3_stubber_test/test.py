import io

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber


def main():
    print("=== boto3_stubber_test ===", flush=True)
    client = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    stubber = Stubber(client)
    stubber.add_response(
        "list_buckets",
        {
            "Buckets": [
                {"Name": "alpha"},
                {"Name": "beta"},
            ],
            "Owner": {"DisplayName": "owner", "ID": "123"},
        },
        {},
    )
    body = StreamingBody(io.BytesIO(b"payload"), len(b"payload"))
    stubber.add_response(
        "get_object",
        {
            "Body": body,
            "ContentLength": 7,
            "ResponseMetadata": {"HTTPStatusCode": 200},
        },
        {"Bucket": "alpha", "Key": "demo.txt"},
    )

    with stubber:
        buckets = [bucket["Name"] for bucket in client.list_buckets()["Buckets"]]
        obj = client.get_object(Bucket="alpha", Key="demo.txt")
        payload = obj["Body"].read().decode("utf-8")

    assert buckets == ["alpha", "beta"]
    assert payload == "payload"
    print(f"buckets={buckets}", flush=True)
    print(f"payload={payload}", flush=True)
    print("boto3 stubber ok", flush=True)


if __name__ == "__main__":
    main()
