"""Standalone in-process record/replay pytest harness.

This is a SEPARATE test harness from ``dockertests/``. The two share some
test bodies because both exercise the same library scenarios, but this file
does not invoke the Docker harness or the Retrace CLI.

Each parametrized case is a Python scenario (copy of a dockertest body, where
that body can honestly run as a same-process function) driven through
:class:`tests.runner.Runner`, which:

1. Records the scenario function under a fresh ``recorder()`` System
   using an in-memory ``IOMemoryTape``.
2. Replays it under a fresh ``replayer()`` System fed by that same tape.
3. Asserts replay's return value equals record's, that record and replay
   raised the same exception (or neither did), and that the tape is fully
   drained (replay consumed exactly what record produced).

A divergence raises :class:`ReplayDivergence` with the tape attached.
Pytest surfaces it as a normal test failure. Each pytest case runs in an
isolated child Python process so failed Retrace patching cannot poison later
cases, but the child still uses the in-process ``Runner`` memory-tape path:
no docker and no ``python -m retracesoftware`` CLI recording/replay. Use
pytest's ``--retrace-config normal|debug`` option to choose the Runner lane.

Scenarios whose core behavior is a process boundary, a long-running server, or
a background worker are not parametrized here. They belong to ``dockertests/``
or focused subprocess tests. If a scenario appears in ``SCENARIOS`` below, this
harness must execute it instead of hiding it behind a skip.

Why dependency imports happen before Runner starts
-------------------------------------------------
Module imports are NOT idempotent inside ``Runner`` because Python caches
modules in ``sys.modules``. If a scenario imported a module on the first call
(record), Retrace could trace import-time side effects. On the second call
(replay), the same import is a no-op, so no events are consumed — leading to
"tape has N unconsumed entries" divergences that are harness artifacts, not
proxy bugs.

The harness therefore pre-imports only the current scenario's dependencies
immediately before recording. That keeps import-time side effects out of the
tape without making every scenario patch every optional library installed in
the active venv.
"""
from __future__ import annotations

import asyncio
import datetime
import importlib
import os
import subprocess
import sys
import time
import traceback
from importlib.util import find_spec
from io import BytesIO
from pathlib import Path
from types import FunctionType
from typing import Optional

import pytest

# Make ``tests.runner`` importable when pytest is invoked from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
DOCKERTESTS_DIR = REPO_ROOT / "dockertests" / "tests"

# Make sure no auto-activation interferes — Runner installs retrace per test.
for _var in ("RETRACE_RECORDING", "RETRACE_CONFIG"):
    os.environ.pop(_var, None)

from tests.runner import Runner  # noqa: E402


_CHILD_SCENARIO_ENV = "RETRACE_DOCKERTEST_INPROCESS_SCENARIO"
_CHILD_TMPDIR_ENV = "RETRACE_DOCKERTEST_INPROCESS_TMPDIR"
_CHILD_RETRACE_CONFIG_ENV = "RETRACE_DOCKERTEST_INPROCESS_CONFIG"
_CHILD_SKIP_CODE = 77
_CHILD_TIMEOUT = int(os.environ.get("RETRACE_DOCKERTEST_INPROCESS_TIMEOUT", "180"))


class ScenarioSkip(Exception):
    pass


# Third-party modules are intentionally loaded per scenario, immediately before
# the Runner starts recording. This keeps import-time side effects out of the
# tape without making every scenario patch every optional library in the venv.
np = None
arrow = None
_black = None
_bytecode = None
Fernet = None
FileLock = None
FileLockTimeout = None
jsonschema_validate = None
JsonSchemaValidationError = None
lz4frame = None
pd = None
BaseModel = None
sync_to_async = None
httpx = None
alru_cache = None
requests = None
coreapi = None
grpc = None
_CacheControl = None
_DictCache = None
_psutil = None


def _import_module(name: str):
    return importlib.import_module(name)


def _prepare_scenario_dependencies(scenario: str) -> bool:
    global np, arrow, _black, _bytecode, Fernet
    global FileLock, FileLockTimeout, jsonschema_validate
    global JsonSchemaValidationError, lz4frame, pd, BaseModel
    global sync_to_async, httpx, alru_cache, requests
    global coreapi, grpc, _CacheControl, _DictCache, _psutil

    try:
        if scenario == "numpy_test":
            np = _import_module("numpy")
        elif scenario == "arrow_test":
            arrow = _import_module("arrow")
        elif scenario == "black_test":
            _black = _import_module("black")
        elif scenario == "bytecode_test":
            _bytecode = _import_module("bytecode")
        elif scenario == "cryptography_test":
            Fernet = _import_module("cryptography.fernet").Fernet
        elif scenario == "filelock_test":
            filelock = _import_module("filelock")
            FileLock = filelock.FileLock
            FileLockTimeout = filelock.Timeout
        elif scenario == "jsonschema_test":
            jsonschema_validate = _import_module("jsonschema").validate
            JsonSchemaValidationError = _import_module(
                "jsonschema.exceptions"
            ).ValidationError
        elif scenario == "lz4_test":
            lz4frame = _import_module("lz4.frame")
        elif scenario == "pandas_test":
            pd = _import_module("pandas")
        elif scenario == "pydantic_test":
            BaseModel = _import_module("pydantic").BaseModel
        elif scenario == "asgiref_test":
            sync_to_async = _import_module("asgiref.sync").sync_to_async
        elif scenario == "asynclruio_test":
            httpx = _import_module("httpx")
            alru_cache = _import_module("async_lru").alru_cache
        elif scenario in {"requests_test", "cachecontrol_test"}:
            requests = _import_module("requests")
            if scenario == "cachecontrol_test":
                _CacheControl = _import_module("cachecontrol").CacheControl
                _DictCache = _import_module("cachecontrol.cache").DictCache
        elif scenario == "coreapi_test":
            coreapi = _import_module("coreapi")
        elif scenario == "grpc_test":
            grpc = _import_module("grpc")
            if not _grpc_protos_available():
                return False
        elif scenario == "memray_test":
            if find_spec("psutil") is not None:
                _psutil = _import_module("psutil")
    except ImportError:
        return False

    return True


# ---------------------------------------------------------------------------
# Scenario adapters — each is a self-contained copy of the matching
# dockertests/tests/<name>/test.py body, returning a deterministic value
# that Runner can compare across record/replay.
# ---------------------------------------------------------------------------

def _scenario_simple_test():
    """Mirror of dockertests/tests/simple_test/test.py."""
    print("=" * 60, flush=True)
    print("Simple Test - No Infrastructure Needed", flush=True)
    print("=" * 60, flush=True)
    assert 1 + 1 == 2, "Math works!"
    print("- Basic math: 1 + 1 = 2", flush=True)
    text = "Hello, Docker!"
    assert text.startswith("Hello"), "String check failed"
    print(f"- String test: '{text}'", flush=True)
    items = [1, 2, 3, 4, 5]
    assert sum(items) == 15, "Sum check failed"
    print(f"- List sum: {items} = {sum(items)}", flush=True)
    print("\n" + "=" * 60, flush=True)
    print("All tests passed!", flush=True)
    print("=" * 60, flush=True)
    return "simple_test:ok"


def _scenario_time1():
    """Mirror of dockertests/tests/time1/test.py."""
    t = time.time()
    print(t, flush=True)
    return "time1:ok"


def _scenario_datetime_test():
    """Mirror of dockertests/tests/datetime_test/test.py."""
    print("=== datetime_test ===", flush=True)

    tz = datetime.timezone.utc
    now = datetime.datetime.now(tz)
    print(f"Now with UTC timezone: {now}", flush=True)
    assert isinstance(now, datetime.datetime)

    local_tz = datetime.timezone(datetime.timedelta(seconds=-time.timezone))
    now_local = datetime.datetime.now(local_tz)
    print(f"Now with local timezone: {now_local}", flush=True)
    assert now_local.tzinfo is not None

    dt1 = datetime.datetime(2023, 10, 2, tzinfo=datetime.timezone.utc)
    dt2 = dt1 + datetime.timedelta(days=10)
    print(f"Shifted datetime: {dt2}", flush=True)
    assert (dt2 - dt1).days == 10

    print("All datetime tests passed.", flush=True)
    return "datetime_test:ok"


def _scenario_asyncio_test():
    """Mirror of dockertests/tests/asyncio_test/test.py."""
    async def async_task(delay: int, message: str):
        await asyncio.sleep(delay)
        return message

    async def main():
        result = await async_task(1, "Hello, asyncio!")
        assert result == "Hello, asyncio!", \
            f"Expected 'Hello, asyncio!' but got '{result}'"
        print("Test passed!", flush=True)

    asyncio.run(main())
    return "asyncio_test:ok"


def _scenario_numpy_test():
    """Mirror of dockertests/tests/numpy_test/test.py."""
    print("=== numpy_test ===", flush=True)
    a = np.array([1, 2, 3])
    b = np.array([4, 5, 6])
    c = a + b
    print("res", c, flush=True)
    assert (c == np.array([5, 7, 9])).all()
    return "numpy_test:ok"


def _scenario_pydantic_test():
    """Mirror of dockertests/tests/pydantic_test/test.py."""
    print("=== pydantic_test ===", flush=True)

    class UserModel(BaseModel):
        id: int
        name: str
        signup_ts: datetime.datetime
        email: Optional[str] = None

    print("Testing Pydantic data validation...", flush=True)
    user_data = {
        "id": 123,
        "name": "Natty Bestpup",
        "signup_ts": "2021-01-01T12:34:56",
    }
    print(f"Input data: {user_data}", flush=True)

    user = UserModel(**user_data)
    print(f"Validated user: {user}", flush=True)
    assert user.id == 123
    assert user.name == "Natty Bestpup"
    assert user.email is None

    user_data_with_email = {
        "id": 456,
        "name": "Alice Smith",
        "signup_ts": "2021-02-15T10:30:00",
        "email": "alice@example.com",
    }
    print(f"\nInput data with email: {user_data_with_email}", flush=True)
    user_with_email = UserModel(**user_data_with_email)
    print(f"Validated user with email: {user_with_email}", flush=True)
    assert user_with_email.email == "alice@example.com"

    dumped = user.model_dump()
    dumped_json = user.model_dump_json()
    print(f"\nModel to dict: {dumped}", flush=True)
    print(f"Model to JSON: {dumped_json}", flush=True)
    print("All Pydantic validation tests completed successfully!", flush=True)
    return "pydantic_test:ok"


def _scenario_cryptography_test():
    """Mirror of dockertests/tests/cryptography_test/test.py."""
    print("=== cryptography_test ===", flush=True)

    key = Fernet.generate_key()
    cipher_suite = Fernet(key)
    message = b"this is rather interesting"

    encrypted_text = cipher_suite.encrypt(message)
    print("Encrypted:", encrypted_text, flush=True)

    decrypted_text = cipher_suite.decrypt(encrypted_text)
    print("Decrypted:", decrypted_text, flush=True)

    assert decrypted_text == message
    return "cryptography_test:ok"


_LZ4_TEST_DATA = b"The quick brown fox jumps over the lazy dog" * 1000


def _scenario_lz4_test():
    """Mirror of dockertests/tests/lz4_test/test.py."""
    print("=== lz4_test ===", flush=True)

    def assert_equal(a, b, label):
        assert a == b, f"[FAIL] {label}"
        print(f"[PASS] {label}", flush=True)

    # basic compression and decompression
    compressed = lz4frame.compress(_LZ4_TEST_DATA)
    decompressed = lz4frame.decompress(compressed)
    assert_equal(decompressed, _LZ4_TEST_DATA, "Basic compression and decompression")

    # custom compression settings
    compressed = lz4frame.compress(
        _LZ4_TEST_DATA,
        compression_level=9,
        block_size=lz4frame.BLOCKSIZE_MAX64KB,
        block_linked=True,
        content_checksum=True,
        block_checksum=True,
        store_size=True,
    )
    decompressed = lz4frame.decompress(compressed)
    assert_equal(decompressed, _LZ4_TEST_DATA, "Custom compression settings")

    # frame info
    compressed = lz4frame.compress(_LZ4_TEST_DATA)
    frame_info = lz4frame.get_frame_info(compressed)
    assert isinstance(frame_info, dict), "Frame info is not a dict"
    assert "block_linked" in frame_info, "Frame info missing 'block_linked' key"
    assert isinstance(frame_info["block_linked"], bool), \
        "Frame info 'block_linked' is not a boolean"
    print("[PASS] Frame info extracted and valid", flush=True)

    # streaming through a BytesIO buffer
    buf = BytesIO()
    with lz4frame.LZ4FrameFile(buf, mode="wb") as f:
        f.write(_LZ4_TEST_DATA)
    buf.seek(0)
    with lz4frame.LZ4FrameFile(buf, mode="rb") as f:
        decompressed_stream = f.read()
    assert_equal(decompressed_stream, _LZ4_TEST_DATA, "Streaming to memory roundtrip")

    return "lz4_test:ok"


def _scenario_bytecode_test():
    """Mirror of dockertests/tests/bytecode_test/test.py."""
    print("=== bytecode_test ===", flush=True)

    code = _bytecode.Bytecode()
    code.extend([
        _bytecode.Instr("LOAD_CONST", 1),
        _bytecode.Instr("RETURN_VALUE"),
    ])

    code_obj = code.to_code()
    generated_function = FunctionType(code_obj, globals())
    result = generated_function()

    assert result == 1, "The function should return 1"
    print("Test passed! Bytecode manipulation works correctly.", flush=True)
    return "bytecode_test:ok"


def _scenario_filelock_test():
    """Mirror of dockertests/tests/filelock_test/test.py."""
    print("=== filelock_test ===", flush=True)
    lock = FileLock("shared_log.txt.lock", timeout=2)
    assert lock.lock_file == "shared_log.txt.lock"
    assert lock.timeout == 2
    assert issubclass(FileLockTimeout, FileLockTimeout)
    print("FileLock configuration objects work.", flush=True)
    return "filelock_test:ok"


def _scenario_arrow_test():
    """Mirror of dockertests/tests/arrow_test/test.py."""
    print("=== arrow_test ===", flush=True)

    now = arrow.now()
    print(f"Now: {now}", flush=True)
    assert isinstance(now, arrow.Arrow)
    assert now.year == arrow.now().year

    date = arrow.get(2023, 10, 2)
    formatted_date = date.format("YYYY-MM-DD")
    print(f"Formatted Date: {formatted_date}", flush=True)
    assert formatted_date == "2023-10-02"

    shifted_date = date.shift(days=+10)
    print(f"Shifted Date: {shifted_date}", flush=True)
    assert shifted_date == arrow.get(2023, 10, 12)

    past = arrow.get(2022, 1, 1)
    humanized = past.humanize()
    print(f"Humanized: {humanized}", flush=True)
    assert len(humanized) > 0

    print("All tests passed successfully!", flush=True)
    return "arrow_test:ok"


_JSONSCHEMA_SCHEMA = {
    "type": "object",
    "properties": {
        "exchange": {"type": "string"},
        "api_key": {"type": "string"},
        "api_secret": {"type": "string"},
        "enable_trading": {"type": "boolean"},
    },
    "required": ["exchange", "api_key", "api_secret", "enable_trading"],
}


def _scenario_jsonschema_test():
    """Mirror of dockertests/tests/jsonschema_test/test.py."""
    print("=== jsonschema_test ===", flush=True)

    def validate_json(data, schema):
        try:
            jsonschema_validate(instance=data, schema=schema)
            print("Validation successful!", flush=True)
        except JsonSchemaValidationError as e:
            print(f"Validation error: {e}", flush=True)

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

    print("Testing valid data:", flush=True)
    validate_json(valid_data, _JSONSCHEMA_SCHEMA)
    print("\nTesting invalid data:", flush=True)
    validate_json(invalid_data, _JSONSCHEMA_SCHEMA)
    return "jsonschema_test:ok"


def _scenario_black_test():
    """Mirror of dockertests/tests/black_test/test.py."""
    print("=== black_test ===", flush=True)
    unformatted_code = "def my_function (a,b):\n    return(a+b)"
    expected_formatted_code = "def my_function(a, b):\n    return a + b\n"

    formatted_code = _black.format_str(unformatted_code, mode=_black.FileMode())
    assert formatted_code == expected_formatted_code, \
        "Black did not format the code as expected"

    print("Formatted code:", flush=True)
    print(formatted_code, flush=True)
    return "black_test:ok"


def _scenario_pandas_test():
    """Mirror of dockertests/tests/pandas_test/test.py."""
    print("=== pandas_test ===", flush=True)
    df = pd.DataFrame({"A": [1, 2, 3], "B": [4, 5, 6]})
    df["C"] = df["A"] + df["B"]
    assert df["C"].tolist() == [5, 7, 9]

    out_path = "test_output.csv"
    df.to_csv(out_path, index=False)
    assert os.path.exists(out_path)

    print("worked worked worked", flush=True)
    return "pandas_test:ok"


def _scenario_asgiref_test():
    """Mirror of dockertests/tests/asgiref_test/test.py."""
    print("=== asgiref_test ===", flush=True)

    def sync_function(x, y):
        return x + y

    async def main():
        async_function = sync_to_async(sync_function, thread_sensitive=False)
        result = await async_function(5, 3)
        assert result == 8, "Expected the sum to be 8"
        print("Test passed! sync_to_async works correctly.", flush=True)

    asyncio.run(main())
    return "asgiref_test:ok"


def _scenario_asynclruio_test():
    """Mirror of dockertests/tests/asynclruio_test/test.py."""
    MOCK_API_URL = "https://jsonplaceholder.typicode.com/posts"

    @alru_cache(maxsize=3)
    async def get_clinician_availability(clinician_id: str):
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{MOCK_API_URL}/{clinician_id}")
            response.raise_for_status()
            data = response.json()
            return {
                "clinician_id": clinician_id,
                "available": data["id"] % 2 == 0,
            }

    async def main():
        for cid in ["1", "2", "3", "4"]:
            r1 = await get_clinician_availability(cid)
            r2 = await get_clinician_availability(cid)
            assert r1 == r2
        await get_clinician_availability("5")
        await get_clinician_availability("1")

    print("=== asynclruio_test ===", flush=True)
    asyncio.run(main())
    return "asynclruio_test:ok"


def _scenario_requests_test():
    """Mirror of dockertests/tests/requests_test/test.py."""
    URL = "https://httpbin.org/get?patient_id=p123&status=active"
    print("=== requests_test ===", flush=True)
    response = requests.get(URL, timeout=10)
    response.raise_for_status()
    data = response.json()
    print("Response Data:", data, flush=True)
    assert data["args"] == {"patient_id": "p123", "status": "active"}
    assert data["url"] == URL
    return "requests_test:ok"


def _scenario_threading_stress_test():
    """Mirror of dockertests/tests/threading_stress_test/test.py (post a34d62a).

    Nathan reduced WORKERS=1 / WORK_ITEMS=8 in the upstream test (was
    24 / 2000); we mirror those defaults here. Output goes directly
    under /tmp so replay materialized opens don't depend on a prior
    mkdir on the live filesystem.
    """
    import hashlib
    import queue
    import threading

    WORK_ITEMS = 8
    WORKERS = 1
    OUT_FILE = Path("/tmp/retrace_threading_stress_inproc.log")

    class SharedState:
        def __init__(self):
            self.lock = threading.Lock()
            self.cond = threading.Condition(self.lock)
            self.counter = 0
            self.ready_workers = 0
            self.total_written = 0

    def stable_payload(i):
        h = hashlib.sha256(f"item:{i}".encode()).hexdigest()
        return f"{i}:{h}\n"

    def worker(wid, q, state, start_event):
        with state.cond:
            state.ready_workers += 1
            state.cond.notify_all()
        start_event.wait()
        local_written = 0
        local_hash = hashlib.sha256()
        while True:
            item = q.get()
            if item is None:
                q.task_done()
                break
            payload = stable_payload(item)
            digest = hashlib.sha256(payload.encode()).digest()
            local_hash.update(digest)
            with state.lock:
                state.counter += 1
            with state.lock:
                with OUT_FILE.open("a", encoding="utf-8") as f:
                    f.write(f"W{wid} " + payload)
                    state.total_written += 1
                    local_written += 1
            q.task_done()
        with state.lock:
            with OUT_FILE.open("a", encoding="utf-8") as f:
                f.write(f"SUMMARY W{wid} written={local_written} hash={local_hash.hexdigest()}\n")

    print("=== threading_stress_test ===", flush=True)
    if OUT_FILE.exists():
        OUT_FILE.unlink()
    q: "queue.Queue" = queue.Queue()
    state = SharedState()
    start_event = threading.Event()
    threads = []
    for wid in range(WORKERS):
        t = threading.Thread(target=worker, args=(wid, q, state, start_event), daemon=True)
        t.start()
        threads.append(t)
    with state.cond:
        while state.ready_workers < WORKERS:
            state.cond.wait(timeout=0.1)
    for i in range(WORK_ITEMS):
        q.put(i)
    start_event.set()
    for _ in range(WORKERS):
        q.put(None)
    q.join()
    for t in threads:
        t.join(timeout=10)
    assert state.counter == WORK_ITEMS
    assert state.total_written == WORK_ITEMS
    return f"threading_stress_test:ok counter={state.counter}"


def _scenario_pyspy_test():
    """Mirror of dockertests/tests/pyspy_test/test.py (post a34d62a).

    Nathan's rewrite: dropped the subprocess.run for `pip show py-spy`
    (replaced with importlib.util.find_spec) and replaced the tempfile
    write with io.StringIO. No subprocess and no real filesystem now.
    """
    import io as _io_mod
    from importlib.util import find_spec

    print("=== pyspy_test ===", flush=True)

    def cpu_intensive_function():
        result = 0
        for i in range(1_000_000):
            result += i * i
        return result

    def memory_intensive_function():
        data = []
        for i in range(10_000):
            data.append([i] * 100)
        return len(data)

    def io_intensive_function():
        buffer = _io_mod.StringIO()
        for i in range(1000):
            buffer.write(f"Line {i}\n")
        buffer.seek(0)
        return sum(1 for _line in buffer)

    print("1. CPU...", flush=True)
    cpu_intensive_function()
    print("2. Memory...", flush=True)
    memory_intensive_function()
    print("3. IO...", flush=True)
    io_intensive_function()
    print("4. py-spy availability...", flush=True)
    print("   present" if find_spec("py_spy") is not None else "   not installed", flush=True)
    return "pyspy_test:ok"


def _scenario_coreapi_test():
    """Mirror of dockertests/tests/coreapi_test/test.py."""
    print("=== coreapi_test ===", flush=True)
    client = coreapi.Client()
    response = client.get("https://api.github.com")
    assert "current_user_url" in response
    print("CoreAPI GET request test passed!", flush=True)
    return "coreapi_test:ok"


def _scenario_grpc_test():
    """Mirror of dockertests/tests/grpc_test/test.py (post a34d62a).

    Nathan's rewrite: no more multiprocessing.Process server, no more
    real grpc.insecure_channel, no more protoc subprocess. Uses an
    InProcessChannel that drives the servicer directly via the
    protobuf serialize/deserialize round-trip. patient_pb2.py /
    patient_pb2_grpc.py are now committed in the repo.
    """
    print("=== grpc_test ===", flush=True)

    test_dir = DOCKERTESTS_DIR / "grpc_test"
    if str(test_dir) not in sys.path:
        sys.path.insert(0, str(test_dir))
    import patient_pb2
    import patient_pb2_grpc

    patients = {
        "p123": {"name": "John Doe", "age": 45, "status": "admitted"},
        "p456": {"name": "Jane Smith", "age": 30, "status": "discharged"},
    }

    class PatientServiceServicer(patient_pb2_grpc.PatientServiceServicer):
        def GetPatientInfo(self, request, context):
            info = patients.get(request.patient_id, {"name": "Unknown", "age": 0, "status": "N/A"})
            return patient_pb2.PatientResponse(name=info["name"], age=info["age"], status=info["status"])

    class FakeRpcContext:
        def __init__(self):
            self.code = grpc.StatusCode.OK
            self.details = ""
        def set_code(self, code):
            self.code = code
        def set_details(self, details):
            self.details = details

    class InProcessChannel:
        def __init__(self, servicer):
            self.servicer = servicer
        def unary_unary(self, method, request_serializer, response_deserializer, **kwargs):
            assert method == "/patient.PatientService/GetPatientInfo"
            def call(request):
                request_bytes = request_serializer(request)
                round_tripped = patient_pb2.PatientRequest.FromString(request_bytes)
                response = self.servicer.GetPatientInfo(round_tripped, FakeRpcContext())
                return response_deserializer(response.SerializeToString())
            return call

    servicer = PatientServiceServicer()
    channel = InProcessChannel(servicer)
    stub = patient_pb2_grpc.PatientServiceStub(channel)
    for pid in ("p123", "p999"):
        response = stub.GetPatientInfo(patient_pb2.PatientRequest(patient_id=pid))
        print("Patient Info:", response, flush=True)
    return "grpc_test:ok"


def _scenario_cachecontrol_test():
    """Mirror of dockertests/tests/cachecontrol_test/test.py."""
    import requests as _rq
    print("=== cachecontrol_test ===", flush=True)
    cache = _DictCache()
    cache.set("demo-key", b"demo-value")
    assert cache.get("demo-key") == b"demo-value"
    session = _CacheControl(_rq.Session(), cache=cache)
    adapter = session.get_adapter("https://example.test/")
    assert adapter.__class__.__name__ == "CacheControlAdapter"
    print("CacheControl session mounted with DictCache.", flush=True)
    return "cachecontrol_test:ok"


def _scenario_memray_test():
    """Mirror of dockertests/tests/memray_test/test.py.

    The dockertest body queries psutil for the current process RSS, which
    pulls macOS task_info on the recorded PID. Replay materializes the
    Process() object and tries the lookup against a PID that no longer
    exists in the live system, which is a known materialization hazard.
    """
    from importlib.util import find_spec

    print("=== memray_test ===", flush=True)
    print("Testing memray memory profiling...", flush=True)
    small_list = [i for i in range(1000)]
    print(f"Created list with {len(small_list)} elements", flush=True)
    medium_list = [i for i in range(10000)]
    print(f"Created list with {len(medium_list)} elements", flush=True)
    lists = [[i for i in range(10000)] for _ in range(10)]
    total = sum(len(lst) for lst in lists)
    print(f"Memory-intensive operation completed, total elements: {total}", flush=True)
    leak_data = [[i] * 100 for i in range(1000)]
    print(f"Created potential leak data with {len(leak_data)} items", flush=True)
    if find_spec("memray") is not None:
        print("memray is available", flush=True)
    else:
        print("memray not installed (expected for this test)", flush=True)
    if _psutil is not None:
        process = _psutil.Process()
        memory_info = process.memory_info()
        print(f"Current memory usage: {memory_info.rss / 1024 / 1024:.2f} MB", flush=True)
    else:
        print("psutil not available for memory reporting", flush=True)
    print("All memray profiling tests completed!", flush=True)
    return "memray_test:ok"


def _scenario_pyopenssl_test():
    """Mirror of dockertests/tests/pyopenssl_test/test.py.

    Spins up an SSL echo server on a fresh port in a daemon thread, then
    a client side does the handshake + a single send/recv. Cert/key are
    embedded literally in the dockertest source.
    """
    import socket as _sock
    import threading as _t
    import tempfile as _tf

    cert_pem = (
        b"-----BEGIN CERTIFICATE-----\n"
        b"MIIDCTCCAfGgAwIBAgIUbgYJEOtiHjJ5pYjqPEN7flMS9RkwDQYJKoZIhvcNAQEL\n"
        b"BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MDQyODE3NDMzOFoXDTM2MDQy\n"
        b"NTE3NDMzOFowFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF\n"
        b"AAOCAQ8AMIIBCgKCAQEA5hCwXMtJwhhwrvP4svAOU+YJEmBKEDry7Ybt6KVXwySl\n"
        b"I0X1r3eGzvKUHkXeriyu1F8XYEpFvehBGaG4EtaidxdXDUTHaY56bWh0Ht6sfXGH\n"
        b"d2zQ+03sAfl0QLo0cBlDXLpgYdx6bzKrqZYWYLQ/z7j8HwKY8ER7dfTIMPRDQ52G\n"
        b"O/QJI1B73FCUSw5pcBmNEM23/ZSGrTWH3mspb9kZL98RTjeKb86IcoDcF/FRQAMM\n"
        b"2Fv2hXI6epU7+M+tlMSl9L6kJ/P1haVsWNW0ZfocfrjKapfmKnLCz30iUv/yEUMX\n"
        b"aOaoDqDkAEF6qnTbMLbQTS3jBws9ykAxNwLArCs16QIDAQABo1MwUTAdBgNVHQ4E\n"
        b"FgQUsuQyYGi78Nt69UmzB1wYo9cA2RIwHwYDVR0jBBgwFoAUsuQyYGi78Nt69Umz\n"
        b"B1wYo9cA2RIwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEACqrH\n"
        b"EL6VW2AdsG2iiEESjjyEQ14oY8gs4N6s5kcUunaRVeOh7VFDTZHCZxRdgyoMKWtk\n"
        b"uHg8sbHCML+OJqbBcSNdrTuOlSLfivV8YoWXtIyVVVQjIW7xhLA/APZWgVzI84DV\n"
        b"SfUQncj1gy5ldoQ6vgFHqov4pEzs3rddhZRsTTzb3w/4rABnEr7GHt2cCPkFh+ad\n"
        b"DZZehGUPYrKsU5M4qef96ZRj00Ekkd1Tgtjjc0sJ31CDuQHYz2IW4IP/oe+oQN+q\n"
        b"G433SCIFXArNkaqB2x3nE/PCpGYXKYjseCLF41X8I1YmWiDSABF3Zm5+bbiTD1gf\n"
        b"0Xn2hYS+/vQW2d4Fww==\n"
        b"-----END CERTIFICATE-----"
    )
    key_pem = (
        b"-----BEGIN PRIVATE KEY-----\n"
        b"MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDmELBcy0nCGHCu\n"
        b"8/iy8A5T5gkSYEoQOvLthu3opVfDJKUjRfWvd4bO8pQeRd6uLK7UXxdgSkW96EEZ\n"
        b"obgS1qJ3F1cNRMdpjnptaHQe3qx9cYd3bND7TewB+XRAujRwGUNcumBh3HpvMqup\n"
        b"lhZgtD/PuPwfApjwRHt19Mgw9ENDnYY79AkjUHvcUJRLDmlwGY0Qzbf9lIatNYfe\n"
        b"aylv2Rkv3xFON4pvzohygNwX8VFAAwzYW/aFcjp6lTv4z62UxKX0vqQn8/WFpWxY\n"
        b"1bRl+hx+uMpql+YqcsLPfSJS//IRQxdo5qgOoOQAQXqqdNswttBNLeMHCz3KQDE3\n"
        b"AsCsKzXpAgMBAAECggEABfGG27bXUL+0LGRRRWWSqAqj9Fs4UONz1Lyj1zZrb1vM\n"
        b"fuvbjBGzEe9yg/+yO4OJ20djnP7+NYjTR2685ElpWutC8RqY118Mj1iWiRjESLh8\n"
        b"nIAkYIrITJGdtgPLv28NXBhChHSxnnY5SzgPm4HO3jZq+0k/rYFYjJkPo8XV9Zc9\n"
        b"vOd+CL/RKJypbGp6j33mWC5pqyqsEQ8ghOA6J0MrLXIFgrqHLmYLKrF+vDgTSvRH\n"
        b"BPYFh4Uix3c/C26qWhJfmK8E5fYEaNHyJe9bdMxltiX+dDGixdFXqy072/Gom6bv\n"
        b"/SXLFFCsmOuLfOVWjYew3V7FR32lO9SXnTFoguIPSwKBgQD0YvP+XLBA5/fFBV/P\n"
        b"16ylPG9YYZYEjK9J8VxhBi3BAcTu4O/I14es8C5OLgKN6ByRJEOPZ4aLFj5hNVna\n"
        b"YVJcaZ8vXHeL/yKv5AOwfNn2snCam5LbvhMUHVcXMUfOyhkIBsfb2VQHMrira/+a\n"
        b"4gWvbC9N6Pe02PuO2baVDz4EfwKBgQDw/4L+lsgIWBfqfPDBaOu54i/CqGrBzZ0K\n"
        b"VRpr0r15+ohpku9m0YRSr+sm7sn/Ogbd16TO9KY0Ye1NKnluu6BCqnKrzcyaQmz9\n"
        b"NiJt5y6blxS382Gw38TA/R4Zjj+/48BTQuAh3unQsTdQPb017/q829J+nQCULa3T\n"
        b"s0wQWiXxlwKBgQDPUvtHeP6VsbUC0fJccs2mSET1p6QLLAaxJi+GqCU8rfGR7gW+\n"
        b"Twps7j16WZIVLSq+/xLJn7wGVtKIySf3GcUzXO+M0Fciz0lwCnIO0XxfyzW4E+9c\n"
        b"uD2bPODbbhVLGyxtIMOAgTjF+oOr+a0YilLkZVUkNVWfeMzAfXZlsk6cpQKBgQDJ\n"
        b"JquCrf2mIUlM+h3FgTqHu0fb9NCulF0IW8IizxJBdqBXZkIWEricf6MJqvPE6P0E\n"
        b"O1KfPspfHIGCD/qtN0PrgPMXfT3SX7Eyo/WWwAhB65dqdmVKyWsjHeH6uKVzF7jW\n"
        b"hhInkzSbcN9XRUDhfT1OVzhZX9g01e+prJTHbUcQXwKBgHIvM83EUBBWO7UaISIj\n"
        b"0aeiC2VjlfXBqLFi6uTS0xIjxGqhpFGOopBFFB44Sn2mxtXuh6/8/E0Tcn3ZFPe2\n"
        b"IoWJxykdeNvKpCBRxuLJzv2jIf0GcI+cDVC+9PjYiPBU63tIHuLxzfKmLRaAacmg\n"
        b"O56SEr2V3q0LyeDez8exQuJS\n"
        b"-----END PRIVATE KEY-----"
    )

    print("=== pyopenssl_test ===", flush=True)
    base_dir = _tf.mkdtemp(prefix="pyopenssl_inproc_")
    cert_path = os.path.join(base_dir, "server.crt")
    key_path = os.path.join(base_dir, "server.key")
    with open(cert_path, "wb") as f:
        f.write(cert_pem)
    with open(key_path, "wb") as f:
        f.write(key_pem)

    port = 8443
    while True:
        try:
            test_sock = _sock.socket()
            test_sock.bind(("127.0.0.1", port))
            test_sock.close()
            break
        except OSError:
            port += 1

    def run_server(port, ready):
        ctx = _SSL.Context(_SSL.TLS_SERVER_METHOD)
        ctx.use_privatekey_file(key_path)
        ctx.use_certificate_file(cert_path)
        s = _sock.socket()
        s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        ready.set()
        conn, _addr = s.accept()
        ssl_conn = _SSL.Connection(ctx, conn)
        ssl_conn.set_accept_state()
        ssl_conn.do_handshake()
        data = ssl_conn.recv(1024)
        ssl_conn.send(b"Echo: " + data)
        ssl_conn.shutdown()
        ssl_conn.close()
        conn.close()
        s.close()

    ready = _t.Event()
    server_t = _t.Thread(target=run_server, args=(port, ready), daemon=True)
    server_t.start()
    ready.wait(timeout=10)

    ctx = _SSL.Context(_SSL.TLS_CLIENT_METHOD)
    ctx.set_verify(_SSL.VERIFY_NONE, lambda *args: True)
    conn = _SSL.Connection(ctx, _sock.socket())
    conn.connect(("127.0.0.1", port))
    conn.set_connect_state()
    conn.do_handshake()
    msg = b"Hello over SSL"
    conn.send(msg)
    data = conn.recv(1024)
    assert b"Echo: " + msg in data, "SSL echo failed"
    conn.shutdown()
    conn.close()
    print("All pyOpenSSL tests passed.", flush=True)
    return "pyopenssl_test:ok"


# Helper used by the grpc scenario dependency check
def _grpc_protos_available() -> bool:
    if grpc is None:
        return False
    pb_dir = DOCKERTESTS_DIR / "grpc_test"
    return (pb_dir / "patient_pb2.py").exists() and (pb_dir / "patient_pb2_grpc.py").exists()


# Scenario id -> function. Dependencies are loaded by
# ``_prepare_scenario_dependencies`` immediately before record/replay starts.
SCENARIOS = {
    "simple_test":          _scenario_simple_test,
    "time1":                _scenario_time1,
    "datetime_test":        _scenario_datetime_test,
    "numpy_test":           _scenario_numpy_test,
    "pydantic_test":        _scenario_pydantic_test,
    "cryptography_test":    _scenario_cryptography_test,
    "lz4_test":             _scenario_lz4_test,
    "bytecode_test":        _scenario_bytecode_test,
    "filelock_test":        _scenario_filelock_test,
    "arrow_test":           _scenario_arrow_test,
    "jsonschema_test":      _scenario_jsonschema_test,
    "black_test":           _scenario_black_test,
    "asyncio_test":         _scenario_asyncio_test,
    "pandas_test":          _scenario_pandas_test,
    "asgiref_test":         _scenario_asgiref_test,
    "asynclruio_test":      _scenario_asynclruio_test,
    "requests_test":        _scenario_requests_test,
    "threading_stress_test":_scenario_threading_stress_test,
    "pyspy_test":           _scenario_pyspy_test,
    "coreapi_test":         _scenario_coreapi_test,
    "grpc_test":            _scenario_grpc_test,
    "cachecontrol_test":    _scenario_cachecontrol_test,
    "memray_test":          _scenario_memray_test,
}

OUT_OF_PROCESS_SCENARIOS = {
    "appnope_test": "uses multiprocessing.Process; covered by dockertests",
    "opentelemetry_test": "uses BatchSpanProcessor background worker thread",
    "flask_test": "requires a live unretraced Flask server boundary",
    "pyopenssl_test": "drives a socket/TLS server thread",
}


# Scenarios that write files to cwd. We isolate them in a per-test tmp
# directory at the test wrapper level (NOT inside the recorded function
# body) so tempfile cleanup events don't enter the replayed tape.
_NEEDS_TMP_CWD = {"filelock_test", "pandas_test"}
def _scenario_pytest_params():
    return [pytest.param(name, id=name) for name in SCENARIOS]


def test_out_of_process_scenarios_are_not_inprocess_cases() -> None:
    """Keep this harness honest: excluded cases must not be silent skips."""
    assert not (set(SCENARIOS) & set(OUT_OF_PROCESS_SCENARIOS))


def _run_scenario_once(scenario: str, tmp_path: Path) -> None:
    fn = SCENARIOS[scenario]
    if not _prepare_scenario_dependencies(scenario):
        raise ScenarioSkip(f"{scenario}: missing required third-party dependency")

    retrace_config = os.environ.get(_CHILD_RETRACE_CONFIG_ENV, "normal")
    if retrace_config == "debug":
        matrix = ({"name": "debug", "debug": True},)
    elif retrace_config == "normal":
        matrix = ({"name": "normal", "debug": False},)
    else:
        raise RuntimeError(f"unknown in-process retrace config: {retrace_config}")

    runner = Runner(matrix=matrix)

    if scenario in _NEEDS_TMP_CWD:
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            replay_result = runner.run(fn)
        finally:
            os.chdir(old_cwd)
    else:
        replay_result = runner.run(fn)

    assert replay_result is not None


def _run_child_scenario_from_env() -> int:
    scenario = os.environ[_CHILD_SCENARIO_ENV]
    tmpdir = Path(os.environ[_CHILD_TMPDIR_ENV])
    try:
        _run_scenario_once(scenario, tmpdir)
    except ScenarioSkip as exc:
        print(f"SKIP: {exc}", flush=True)
        return _CHILD_SKIP_CODE
    except BaseException:
        traceback.print_exc()
        return 1
    return 0


@pytest.mark.parametrize("scenario", _scenario_pytest_params())
def test_dockertest_inprocess(scenario: str, tmp_path: Path, request) -> None:
    """Record the scenario in-process, then replay it.

    Pure proxy-layer record/replay inside an isolated child process — no
    docker and no ``python -m retracesoftware`` CLI recording/replay.
    Failure raises ``ReplayDivergence`` with the tape attached.

    Each scenario runs in a child Python process. Retrace intentionally patches
    process-global module/type state, and a failing scenario may leave that
    state partially patched. Child isolation keeps one real product failure
    from turning every later parametrized case into a harness artifact.
    """
    env = os.environ.copy()
    env[_CHILD_SCENARIO_ENV] = scenario
    env[_CHILD_TMPDIR_ENV] = str(tmp_path)
    env[_CHILD_RETRACE_CONFIG_ENV] = request.config.getoption("--retrace-config")
    env.pop("RETRACE_RECORDING", None)
    env.pop("RETRACE_CONFIG", None)

    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=_CHILD_TIMEOUT,
    )
    if proc.returncode == _CHILD_SKIP_CODE:
        pytest.skip(proc.stdout.strip() or f"{scenario}: skipped in child process")

    assert proc.returncode == 0, (
        f"{scenario} child record/replay failed with exit code {proc.returncode}\n"
        f"--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )


if __name__ == "__main__" and _CHILD_SCENARIO_ENV in os.environ:
    raise SystemExit(_run_child_scenario_from_env())
