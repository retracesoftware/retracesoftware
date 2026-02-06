import os
import tempfile

import fsspec


def test_fsspec_operations():
    """Test fsspec filesystem operations."""
    print("Testing fsspec filesystem operations...", flush=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Using temporary directory: {temp_dir}", flush=True)

        fs = fsspec.filesystem("file")

        test_file = os.path.join(temp_dir, "test.txt")
        test_content = "Hello, fsspec! This is a test file."

        print("\n1. Testing file writing...", flush=True)
        with fs.open(test_file, "w") as f:
            f.write(test_content)
        print(f"Wrote file: {test_file}", flush=True)

        print("\n2. Testing file reading...", flush=True)
        with fs.open(test_file, "r") as f:
            content = f.read()
        print(f"Read content: {content}", flush=True)
        assert content == test_content

        print("\n3. Testing directory listing...", flush=True)
        files = fs.ls(temp_dir)
        print(f"Files in directory: {files}", flush=True)

        print("\n4. Testing file info...", flush=True)
        info = fs.info(test_file)
        print(f"File info: size={info['size']}, type={info['type']}", flush=True)

        print("\n5. Testing file copying...", flush=True)
        copy_file = os.path.join(temp_dir, "test_copy.txt")
        fs.copy(test_file, copy_file)
        print(f"Copied file to: {copy_file}", flush=True)

        print("\n6. Testing file moving...", flush=True)
        move_file = os.path.join(temp_dir, "test_moved.txt")
        fs.move(copy_file, move_file)
        print(f"Moved file to: {move_file}", flush=True)

        print("\n7. Testing file existence...", flush=True)
        exists = fs.exists(test_file)
        print(f"Original file exists: {exists}", flush=True)
        assert exists is True

        print("\n8. Testing file removal...", flush=True)
        fs.rm(move_file)
        exists_after_rm = fs.exists(move_file)
        print(f"File removed, exists: {exists_after_rm}", flush=True)
        assert exists_after_rm is False

        print("\n9. Testing memory filesystem...", flush=True)
        mem_fs = fsspec.filesystem("memory")
        mem_file = "test_memory.txt"

        with mem_fs.open(mem_file, "w") as f:
            f.write("Memory file content")

        with mem_fs.open(mem_file, "r") as f:
            mem_content = f.read()
        print(f"Memory file content: {mem_content}", flush=True)
        assert mem_content == "Memory file content"

        print("\nAll fsspec operations tested successfully!", flush=True)


if __name__ == "__main__":
    print("=== fsspec_test ===", flush=True)
    test_fsspec_operations()
