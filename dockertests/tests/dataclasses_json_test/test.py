from dataclasses import dataclass

from dataclasses_json import dataclass_json


@dataclass_json
@dataclass
class Event:
    name: str
    count: int


def main():
    print("=== dataclasses_json_test ===")
    event = Event("record", 3)
    encoded = event.to_json()
    decoded = Event.from_json(encoded)
    assert decoded == event
    print(encoded)
    print(f"event={decoded.name} count={decoded.count}")
    print("dataclasses-json ok")


if __name__ == "__main__":
    main()
