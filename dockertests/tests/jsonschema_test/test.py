from jsonschema import validate
from jsonschema.exceptions import ValidationError


def validate_json(data, schema):
    try:
        validate(instance=data, schema=schema)
        print("Validation successful!", flush=True)
    except ValidationError as e:
        print(f"Validation error: {e}", flush=True)


schema = {
    "type": "object",
    "properties": {
        "exchange": {"type": "string"},
        "api_key": {"type": "string"},
        "api_secret": {"type": "string"},
        "enable_trading": {"type": "boolean"},
    },
    "required": ["exchange", "api_key", "api_secret", "enable_trading"],
}

valid_data = {
    "exchange": "binance",
    "api_key": "yourapikey123",
    "api_secret": "yoursecretkey123",
    "enable_trading": True,
}

invalid_data = {
    "exchange": "binance",
    "api_key": "yourapikey123",
    "api_secret": "yoursecretkey123",
}


def test_jsonschema_validation():
    print("Testing valid data:", flush=True)
    validate_json(valid_data, schema)

    print("\nTesting invalid data:", flush=True)
    validate_json(invalid_data, schema)


if __name__ == "__main__":
    print("=== jsonschema_test ===", flush=True)
    test_jsonschema_validation()
