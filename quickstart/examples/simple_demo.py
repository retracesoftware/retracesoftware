from datetime import datetime, timezone
import os
import random
import uuid


def main():
    print("=== Retrace simple demo ===", flush=True)
    print(f"cwd={os.getcwd()}", flush=True)
    print(f"now={datetime.now(timezone.utc).isoformat()}", flush=True)
    print(f"random={random.randint(1000, 9999)}", flush=True)
    print(f"uuid={uuid.uuid4()}", flush=True)
    print("Replay should print the same values.", flush=True)


if __name__ == "__main__":
    main()
