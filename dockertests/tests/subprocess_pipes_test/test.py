import subprocess


def main():
    print("=== subprocess_pipes_test ===", flush=True)
    child_script = (
        "data=$(cat); "
        "printf '%s' \"$data\" | tr '[:lower:]' '[:upper:]'; "
        "printf 'child-stderr\\n' >&2"
    )
    proc = subprocess.Popen(
        ["/bin/sh", "-c", child_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate("pipe payload", timeout=5)

    assert proc.returncode == 0
    assert stdout == "PIPE PAYLOAD"
    assert stderr.strip() == "child-stderr"
    print(f"stdout={stdout}", flush=True)
    print(f"stderr={stderr.strip()}", flush=True)
    print("subprocess pipes ok", flush=True)


if __name__ == "__main__":
    main()
