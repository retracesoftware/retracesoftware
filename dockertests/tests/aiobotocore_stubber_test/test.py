import asyncio
from datetime import datetime, timezone

from aiobotocore.session import get_session
from botocore.stub import Stubber


async def main_async():
    print("=== aiobotocore_stubber_test ===")
    session = get_session()
    async with session.create_client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    ) as client:
        stubber = Stubber(client)
        stubber.add_response(
            "list_buckets",
            {
                "Buckets": [
                    {
                        "Name": "retrace-demo",
                        "CreationDate": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    }
                ],
                "Owner": {"DisplayName": "retrace", "ID": "owner-id"},
            },
            {},
        )
        stubber.activate()
        try:
            response = await client.list_buckets()
        finally:
            stubber.deactivate()

    assert [bucket["Name"] for bucket in response["Buckets"]] == ["retrace-demo"]
    assert response["Owner"]["ID"] == "owner-id"
    print("aiobotocore stubbed client record/replay scenario ok")


if __name__ == "__main__":
    asyncio.run(main_async())
