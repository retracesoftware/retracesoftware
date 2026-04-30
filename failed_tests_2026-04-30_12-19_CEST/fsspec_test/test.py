import fsspec


def test_fsspec_operations():
    """Test fsspec filesystem operations."""
    print("Testing fsspec filesystem operations...", flush=True)

    fs = fsspec.filesystem("memory")
    test_file = "/retrace/test.txt"
    copy_file = "/retrace/test_copy.txt"
    move_file = "/retrace/test_moved.txt"
    test_content = "Hello, fsspec! This is a test file."

    print("\n1. Testing memory file writing...", flush=True)
    with fs.open(test_file, "w") as f:
        f.write(test_content)
    print(f"Wrote file: {test_file}", flush=True)

    print("\n2. Testing memory file reading...", flush=True)
    with fs.open(test_file, "r") as f:
        content = f.read()
    print(f"Read content: {content}", flush=True)
    assert content == test_content

    print("\n3. Testing directory listing...", flush=True)
    files = fs.ls("/retrace", detail=False)
    print(f"Files in directory: {files}", flush=True)
    assert test_file in files

    print("\n4. Testing file info...", flush=True)
    info = fs.info(test_file)
    print(f"File info: size={info['size']}, type={info['type']}", flush=True)
    assert info["size"] == len(test_content)
    assert info["type"] == "file"

    print("\n5. Testing file copying...", flush=True)
    fs.copy(test_file, copy_file)
    print(f"Copied file to: {copy_file}", flush=True)
    assert fs.cat(copy_file).decode() == test_content

    print("\n6. Testing file moving...", flush=True)
    fs.move(copy_file, move_file)
    print(f"Moved file to: {move_file}", flush=True)
    assert fs.exists(move_file)

    print("\n7. Testing file existence...", flush=True)
    exists = fs.exists(test_file)
    print(f"Original file exists: {exists}", flush=True)
    assert exists is True

    print("\n8. Testing file removal...", flush=True)
    fs.rm(move_file)
    exists_after_rm = fs.exists(move_file)
    print(f"File removed, exists: {exists_after_rm}", flush=True)
    assert exists_after_rm is False

    print("\nAll fsspec operations tested successfully!", flush=True)


if __name__ == "__main__":
    print("=== fsspec_test ===", flush=True)
    test_fsspec_operations()
