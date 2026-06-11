import boto3
from botocore.config import Config
from moto import mock_aws


def main():
    print("=== moto_inprocess_mock_aws_test ===", flush=True)
    with mock_aws():
        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
            config=Config(retries={"max_attempts": 1, "mode": "standard"}),
        )
        s3.create_bucket(Bucket="retrace-inprocess-moto")
        s3.put_object(
            Bucket="retrace-inprocess-moto",
            Key="invoice.txt",
            Body=b"total=42",
        )
        response = s3.get_object(
            Bucket="retrace-inprocess-moto",
            Key="invoice.txt",
        )
        assert response["Body"].read() == b"total=42"
    print("moto in-process mock_aws ok", flush=True)


if __name__ == "__main__":
    main()
