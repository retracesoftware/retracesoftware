import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


def main():
    print("=== python_dotenv_test ===")
    env_path = Path(".env.retrace_test")
    env_path.write_text("RETRACE_DEMO_NAME=Ada\nRETRACE_DEMO_COUNT=3\n", encoding="utf-8")

    values = dotenv_values(env_path)
    assert values["RETRACE_DEMO_NAME"] == "Ada"
    assert values["RETRACE_DEMO_COUNT"] == "3"

    load_dotenv(env_path, override=True)
    print(f"name={os.environ['RETRACE_DEMO_NAME']} count={os.environ['RETRACE_DEMO_COUNT']}")
    assert os.environ["RETRACE_DEMO_NAME"] == "Ada"
    print("python-dotenv ok")


if __name__ == "__main__":
    main()
