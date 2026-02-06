from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserModel(BaseModel):
    id: int
    name: str
    signup_ts: datetime
    email: Optional[str] = None


def test_pydantic_validation():
    print("Testing Pydantic data validation...", flush=True)

    user_data = {
        "id": 123,
        "name": "Natty Bestpup",
        "signup_ts": "2021-01-01T12:34:56",
    }
    print(f"Input data: {user_data}", flush=True)

    user = UserModel(**user_data)
    print(f"Validated user: {user}", flush=True)
    assert user.id == 123
    assert user.name == "Natty Bestpup"
    assert user.email is None

    user_data_with_email = {
        "id": 456,
        "name": "Alice Smith",
        "signup_ts": "2021-02-15T10:30:00",
        "email": "alice@example.com",
    }
    print(f"\nInput data with email: {user_data_with_email}", flush=True)
    user_with_email = UserModel(**user_data_with_email)
    print(f"Validated user with email: {user_with_email}", flush=True)
    assert user_with_email.email == "alice@example.com"

    # Pydantic v2 methods (this repo's test uses model_dump/model_dump_json).
    dumped = user.model_dump()
    dumped_json = user.model_dump_json()
    print(f"\nModel to dict: {dumped}", flush=True)
    print(f"Model to JSON: {dumped_json}", flush=True)

    print("All Pydantic validation tests completed successfully!", flush=True)


if __name__ == "__main__":
    print("=== pydantic_test ===", flush=True)
    test_pydantic_validation()
