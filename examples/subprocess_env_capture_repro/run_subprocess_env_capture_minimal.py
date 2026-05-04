import os
import subprocess
import sys


def main():
    env = os.environ.copy()
    env["RETRACE_CHILD_MULTIPLIER"] = "7"
    proc = subprocess.run(
        [sys.executable, "child_env_worker.py", "alpha"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    print("CAPTURED", proc.stdout.strip(), proc.stderr.strip(), proc.returncode)


if __name__ == "__main__":
    main()
