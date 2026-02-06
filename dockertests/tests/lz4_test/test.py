import os
import sys
from io import BytesIO

import lz4.frame


TEST_DATA = b"The quick brown fox jumps over the lazy dog" * 1000


def assert_equal(a, b, label):
    if a != b:
        print(f"[FAIL] {label}", flush=True)
        sys.exit(1)
    print(f"[PASS] {label}", flush=True)


def test_basic_compression_and_decompression():
    compressed = lz4.frame.compress(TEST_DATA)
    decompressed = lz4.frame.decompress(compressed)
    assert_equal(decompressed, TEST_DATA, "Basic compression and decompression")


def test_custom_compression_settings():
    compressed = lz4.frame.compress(
        TEST_DATA,
        compression_level=9,
        block_size=lz4.frame.BLOCKSIZE_MAX64KB,
        block_linked=True,
        content_checksum=True,
        block_checksum=True,
        store_size=True,
    )
    decompressed = lz4.frame.decompress(compressed)
    assert_equal(decompressed, TEST_DATA, "Custom compression settings")


def test_frame_info():
    compressed = lz4.frame.compress(TEST_DATA)
    frame_info = lz4.frame.get_frame_info(compressed)
    if frame_info is None or not isinstance(frame_info, dict):
        print("[FAIL] Frame info is not a dict or is None", flush=True)
        sys.exit(1)
    if "block_linked" not in frame_info:
        print("[FAIL] Frame info missing 'block_linked' key", flush=True)
        sys.exit(1)
    if not isinstance(frame_info["block_linked"], bool):
        print("[FAIL] Frame info 'block_linked' is not a boolean", flush=True)
        sys.exit(1)
    print("[PASS] Frame info extracted and valid", flush=True)


def test_streaming_to_memory():
    bio = BytesIO()
    with lz4.frame.open(bio, mode="wb") as f:
        f.write(TEST_DATA)
    bio.seek(0)
    with lz4.frame.open(bio, mode="rb") as f:
        data = f.read()
    assert_equal(data, TEST_DATA, "Streaming to memory")


def test_compress_to_file():
    tmp_path = "lz4_test_temp_file"
    try:
        with lz4.frame.open(tmp_path, mode="wb") as f:
            f.write(TEST_DATA)
        with lz4.frame.open(tmp_path, mode="rb") as f:
            data = f.read()
        assert_equal(data, TEST_DATA, "Compression to file and back")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_context_manager():
    stream = BytesIO()
    with lz4.frame.open(stream, mode="wb") as writer:
        writer.write(TEST_DATA)
    stream.seek(0)
    with lz4.frame.open(stream, mode="rb") as reader:
        data = reader.read()
    assert_equal(data, TEST_DATA, "Context manager behavior")


def test_empty_input():
    compressed = lz4.frame.compress(b"")
    decompressed = lz4.frame.decompress(compressed)
    assert_equal(decompressed, b"", "Empty input compression")


def test_invalid_decompression():
    try:
        lz4.frame.decompress(b"invalid data")
    except Exception:
        print("[PASS] Invalid input raised exception", flush=True)
        return
    print("[FAIL] Invalid input did not raise exception", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    print("=== lz4_test ===", flush=True)
    test_basic_compression_and_decompression()
    test_custom_compression_settings()
    test_frame_info()
    test_streaming_to_memory()
    test_compress_to_file()
    test_context_manager()
    test_empty_input()
    test_invalid_decompression()
    print("\nAll tests passed.", flush=True)
