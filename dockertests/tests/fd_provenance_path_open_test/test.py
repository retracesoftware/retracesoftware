import os


def main():
    untraced_tmp = "/root/retrace-untraced-tmp"
    os.makedirs(untraced_tmp, exist_ok=True)

    keep = [
        os.open(f"/tmp/retrace-fd-drift-{os.getpid()}-{i}", os.O_CREAT | os.O_RDWR, 0o600)
        for i in range(2)
    ]

    probe_path = os.path.join(untraced_tmp, "probe")
    fd = os.open(probe_path, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
    os.write(fd, b"blat")
    os.close(fd)

    with open(probe_path, "rb") as f:
        assert f.read() == b"blat"

    print(f"fd provenance path open ok {keep} {probe_path}", flush=True)


if __name__ == "__main__":
    print("=== fd_provenance_path_open_test ===", flush=True)
    main()
