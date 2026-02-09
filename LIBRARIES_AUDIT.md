# Third-Party Libraries Audit

Audit of popular Python libraries for non-deterministic behavior that could affect record/replay. The key question for each: does the library have C extensions that make direct syscalls (bypassing already-proxied `posix`, `_socket`, `_ssl`, `select`, etc.), or is it pure Python covered transitively?

---

## Database Drivers

### psycopg3 (psycopg) — NEEDS PROXYING

All variants (ctypes, Cython, binary) wrap **libpq**, which does all network I/O in C via direct `socket()`/`connect()`/`send()`/`recv()` syscalls. psycopg3 only monitors libpq's socket fd for readiness (via `select`/asyncio), but the actual data transfer happens inside libpq's C code, completely bypassing `_socket`.

**Proxy target:** `psycopg.pq.PGconn` interface (works for all backends) or `psycopg_c._pq` (Cython variant).

| Function | What it does |
|---|---|
| `PGconn.connect_start()` / `connect_poll()` | Connection establishment (libpq creates socket, TCP+TLS handshake) |
| `PGconn.send_query()` / `send_query_params()` / `send_query_prepared()` | Enqueue query, may call `send()` |
| `PGconn.consume_input()` | Calls `recv()` to read data from server |
| `PGconn.flush()` | Calls `send()` to flush pending output |
| `PGconn.get_result()` | Returns parsed result (may trigger reads) |
| `PGconn.exec_()` / `exec_params()` | Synchronous exec = send + flush + consume + get_result |

### mysqlclient (MySQLdb) — NEEDS PROXYING

The `_mysql` C extension wraps **libmysqlclient**, which does all network I/O in C. Identical pattern to psycopg2.

**Proxy target:** `MySQLdb._mysql` (aka `_mysql`).

| Function | Underlying C call |
|---|---|
| `_mysql.connect()` | `mysql_real_connect()` — creates socket, TCP+TLS, auth |
| `connection.query()` | `mysql_real_query()` — sends query over socket |
| `connection.store_result()` | `mysql_store_result()` — reads entire result set |
| `connection.use_result()` | `mysql_use_result()` — sets up row-by-row reading |
| `result.fetch_row()` | `mysql_fetch_row()` — reads rows |
| `connection.next_result()` | `mysql_next_result()` — multi-statement results |
| `connection.ping()` | `mysql_ping()` — sends ping packet |
| `connection.close()` | `mysql_close()` — TCP teardown |
| `connection.commit()` | Sends `COMMIT` via `mysql_real_query()` |
| `connection.rollback()` | Sends `ROLLBACK` via `mysql_real_query()` |

### cx_Oracle — NEEDS PROXYING

C extension wrapping OCI (Oracle Call Interface). All I/O happens in the Oracle Client C libraries.

### oracledb — PARTIALLY COVERED

- **Thin mode** (default since oracledb 1.0): COVERED TRANSITIVELY. Despite being Cython-compiled, thin mode uses Python socket objects for all I/O (`self._transport.recv()`, `self._transport.send()`). Cython is for protocol parsing performance.
- **Thick mode**: NEEDS PROXYING. Wraps ODPI-C/OCI in C with GIL released. All I/O-bearing functions (`dpiConn_create`, `dpiStmt_execute`, `dpiStmt_fetch`, etc.) bypass Python entirely. Proxy target: `oracledb.thick_impl`.

### asyncpg — COVERED TRANSITIVELY

Despite being Cython-compiled, asyncpg uses asyncio's transport/protocol abstraction for all network I/O. The Cython code is purely for PostgreSQL wire protocol parsing. Socket created by `loop.create_connection()` which uses `_socket`.

### pymysql — COVERED TRANSITIVELY

Pure Python. All I/O flows through `socket.create_connection()` → `_socket`. TLS uses `ssl.wrap_socket()` → `_ssl`.

### pymongo — COVERED TRANSITIVELY

C extensions (`bson._cbson`, `pymongo._cmessage`) are BSON serialization and wire protocol formatting only — no I/O. All network I/O happens in Python using `socket.create_connection()`.

### redis-py — COVERED TRANSITIVELY

Pure Python. Optional `hiredis` C extension is a parser only (`reader.feed(data)` on in-memory buffers) — no socket handle, no I/O.

### aiomysql — COVERED TRANSITIVELY

Pure Python async wrapper on top of pymysql. All I/O flows through pymysql's socket calls.

### aiosqlite — COVERED TRANSITIVELY

Wraps `sqlite3` (which uses `_sqlite3`, already proxied) by running it in a thread executor.

### SQLAlchemy — COVERED TRANSITIVELY

Pure Python ORM/SQL toolkit. C extensions are for result processing performance only. All I/O delegated to underlying driver.

---

## HTTP / Network

### grpcio — NEEDS PROXYING (Critical)

The gRPC C core performs **ALL** network I/O in C, completely bypassing Python's `_socket`, `_ssl`, and `select`. The entire C core is statically linked into `grpc._cython.cygrpc`.

Direct syscalls in C:
- `socket()` / `connect()` — connection creation
- `read()` / `write()` / `sendmsg()` / `recvmsg()` — data transfer
- `epoll_wait()` / `poll()` — event notification (replaces `select`)
- DNS via c-ares — bypasses `socket.getaddrinfo`
- TLS via BoringSSL — bypasses Python `_ssl`

**Options:**
1. Proxy at the POSIX syscall level (LD_PRELOAD or ptrace)
2. Block grpcio and require REST/HTTP transports instead

### uvloop — NEEDS PROXYING (Critical)

Replaces asyncio's event loop with libuv. The transport/protocol path (`create_connection`, `create_server`) completely bypasses `_socket` and `select`.

- Socket creation: `uv_tcp_init_ex()` → C `socket()` syscall
- Connect: `uv_tcp_connect()` → C `connect()` directly
- Read: `uv_read_start()` → C `read()` directly
- Write (fast path): direct POSIX `write()` syscall
- Event notification: libuv's internal `epoll`/`kqueue` — bypasses Python `select`
- DNS: `uv_getaddrinfo()` — libuv's own threadpool resolver

**Options:**
1. Proxy at POSIX syscall level
2. Block uvloop — most asyncio libraries work fine on the default `SelectorEventLoop`

### gevent — PARTIALLY COVERED

- **Socket data I/O: COVERED.** `_wrefsocket` subclasses `_socket.socket`. All `recv()`/`send()`/`connect()` go through proxied `_socket`.
- **Event notification: NOT COVERED.** Uses libev/libuv C extensions for readiness polling, bypassing Python's `select`.
- **DNS: PARTIALLY COVERED.** Thread resolver (stdlib) is covered. c-ares resolver bypasses `_socket.getaddrinfo`.

### google-cloud-* — PARTIALLY COVERED

| Transport | Status |
|-----------|--------|
| gRPC (default for most services) | **NEEDS PROXYING** — same as grpcio |
| REST/urllib3 (available as alternative) | COVERED — pure Python through `_socket`/`_ssl` |
| REST/requests (available as alternative) | COVERED — pure Python through urllib3 |

**Recommendation:** Force REST transport via configuration, or solve grpcio proxying.

### confluent-kafka — NEEDS PROXYING

Wraps `librdkafka` in C. All network I/O happens in C, bypassing `_socket`. (Distinct from `kafka-python` which is pure Python.)

### requests — COVERED TRANSITIVELY

Pure Python. Delegates all HTTP to urllib3 → `_socket` / `_ssl`.

### urllib3 — COVERED TRANSITIVELY

Pure Python. Uses `socket.create_connection()` and `ssl.wrap_socket()`.

### httpx — COVERED TRANSITIVELY

Pure Python. Uses httpcore for transport (sync uses `socket`, async uses `anyio` → asyncio → `_socket`).

### httpcore — COVERED TRANSITIVELY

Pure Python. All backends go through `_socket` / `_ssl`.

### aiohttp — COVERED TRANSITIVELY

C extensions (`_http_parser`, `_http_writer`, `_websocket`) are parsing/formatting only — no I/O. Network I/O goes through asyncio transports → `_socket`. **Caveat:** if paired with uvloop, uvloop's issues apply.

### websockets — COVERED TRANSITIVELY

C extension (`speedups.c`) only does WebSocket frame masking (byte-level XOR). All I/O through asyncio or threading → `_socket`.

### paramiko — COVERED TRANSITIVELY

Pure Python SSH implementation. Uses `socket` module. Crypto via `cryptography` (compute only, no network I/O).

### pika — COVERED TRANSITIVELY

Pure Python AMQP 0-9-1 client. All I/O through `socket` module.

### kafka-python — COVERED TRANSITIVELY

Pure Python Kafka client. Uses `socket` and `select`.

### boto3 / botocore — COVERED TRANSITIVELY

Pure Python AWS SDK. HTTP transport uses urllib3 → `_socket` / `_ssl`.

---

## Data / Compute

### numpy — COVERED TRANSITIVELY (with caveats)

- **numpy.random**: Has its own C-level PRNG (MT19937, PCG64, etc.) that does NOT use `_random`. However, auto-seeding calls `os.urandom()` → proxied via `posix.urandom`. Given same seed, the PRNG is deterministic.
- **File I/O** (`np.load`/`np.save`): Uses Python `open()` → `posix` (proxied).
- **Math operations**: Deterministic given same inputs (IEEE 754).
- **Caveat — BLAS parallelism**: `np.dot()` on large matrices can produce non-bit-identical results with multithreaded BLAS (OpenBLAS/MKL) due to nondeterministic reduction ordering. Mitigate with `OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1`.

### pandas — COVERED TRANSITIVELY

- `pd.Timestamp.now()` → `datetime.datetime.now()` → proxied via `_datetime`
- `pd.read_csv()` → C parser receives file handles from Python I/O → `posix` (proxied)
- `pd.read_sql()` → DB-API/SQLAlchemy → `_socket` (proxied)
- All operations deterministic given same input data.

### scipy — COVERED TRANSITIVELY

Random sampling delegates to `numpy.random`. Optimization/linear algebra deterministic (same BLAS caveat as numpy).

### scikit-learn — COVERED TRANSITIVELY

All randomness via `numpy.random` (`random_state` parameter pattern). Cython extensions do deterministic compute only.

### torch (PyTorch) — NEEDS PROXYING

Multiple non-deterministic subsystems:

| Source | Details |
|--------|---------|
| **C++ RNG** | Own Mersenne Twister in C++. `torch.rand()`, `torch.randn()`, etc. bypass both `_random` and numpy |
| **Default seeding** | Seeds from `std::random_device` → `getrandom(2)` or `/dev/urandom` at C++ level, **bypasses `posix.urandom`** |
| **CUDA non-determinism** | `atomicAdd`, cuDNN autotuning, scatter/gather operations |
| **ATen thread pool** | Internal CPU parallelism may affect float reduction order |

**Recommended approach:** Force `torch.manual_seed()` with a recorded seed + `torch.use_deterministic_algorithms(True)` + `CUBLAS_WORKSPACE_CONFIG`.

### tensorflow — NEEDS PROXYING

Similar to PyTorch:

| Source | Details |
|--------|---------|
| **C++ RNG** | `PhiloxRandom` in C++ for `tf.random.*` ops |
| **Default seeding** | Seeds from `/dev/urandom` at C++ level, bypasses `posix.urandom` |
| **GPU non-determinism** | Nondeterministic reductions, scatter ops |

**Recommended approach:** Force `tf.random.set_seed()` + `tf.config.experimental.enable_op_determinism()`.

---

## Crypto / Auth

### cryptography — NEEDS PROXYING

OpenSSL's `RAND_bytes()` obtains entropy via `getrandom(2)` or direct `open("/dev/urandom")` + `read()` at the C level, **completely bypassing** `posix.urandom`.

**Affected operations:**
- `Fernet.generate_key()`
- RSA/EC key generation (`rsa.generate_private_key()`, etc.)
- AEAD encryption (auto-generated nonces)
- TLS session setup (partially covered if going through Python's `ssl` module)

**Options:**
1. Intercept at `_openssl_lib` cffi binding level — proxy `RAND_bytes`, `RAND_pseudo_bytes`
2. Replace OpenSSL's RAND method with a custom engine calling back to Python's `os.urandom` (which is proxied)
3. Proxy `cryptography.hazmat.backends.openssl.backend._lib.RAND_bytes`

Note: The existing `_ssl` proxy covers TLS-level randomness going through Python's `ssl` module. The gap is direct `cryptography` API usage.

### bcrypt — COVERED TRANSITIVELY

Salt generation (`bcrypt.gensalt()`) calls `os.urandom(16)` in Python → proxied via `posix.urandom`. Hashing is deterministic given same password + salt.

### PyJWT — COVERED TRANSITIVELY

Pure Python. Uses `json`, `base64`, `hmac`, `hashlib`. For RSA/EC, delegates to `cryptography`.

---

## Serialization / Parsing

### protobuf — PARTIALLY COVERED

The C++ extension (`google.protobuf.pyext._message`) uses hash maps for `map<K,V>` fields. Iteration/serialization order of map entries is **non-deterministic across runs** (hash randomization).

- `SerializeToString()` default: non-deterministic for messages with map fields
- `SerializeToString(deterministic=True)`: sorts map keys, deterministic
- Messages without map fields: deterministic

**Recommendation:** Force `deterministic=True` globally via monkey-patching `SerializeToString`.

### lxml — NEEDS PROXYING

lxml wraps libxml2/libxslt, which do **direct C-level file I/O**:

| Code path | Status |
|-----------|--------|
| `etree.parse("filename.xml")` | libxml2 calls `fopen()`/`fread()` directly in C — **bypasses posix** |
| `etree.parse("http://...")` | libxml2 has built-in HTTP client with direct `socket()`/`connect()` — **bypasses _socket** |
| `etree.parse(file_object)` | libxml2 uses Python callback to read from file object — **covered** |
| `etree.fromstring(data)` | In-memory parsing — **deterministic** |
| XSLT `generate-id()` | Produces pointer-based IDs — **non-deterministic** |

**Recommendation:** Wrap at Python level to force file-object path (open file in Python, pass object to parser).

### Pillow (PIL) — PARTIALLY COVERED

- Normal path: `Image.open(filename)` → Python opens file → decoder reads from Python file object → `posix` (covered).
- `_imaging.map_buffer`: Does `mmap()` at the C level for memory-mapped access — **bypasses posix**.
- All image processing operations: deterministic.

**Recommendation:** Either proxy `PIL._imaging.map_buffer` or disable mmap path.

### msgpack — DETERMINISTIC

C extension is a pure serializer/deserializer. Same inputs → same output.

### orjson — DETERMINISTIC

Rust extension. Deterministic JSON serialization. Preserves dict insertion order.

### ujson — DETERMINISTIC

C extension. Deterministic JSON serialization.

### pydantic — DETERMINISTIC

Rust core (`pydantic_core`) does deterministic validation/serialization.

---

## Task Queues / Messaging

### celery — COVERED TRANSITIVELY

Pure Python on top of kombu. Task IDs via `uuid.uuid4()` → `os.urandom` (proxied).

### kombu — COVERED TRANSITIVELY

Pure Python. All transports (AMQP, Redis, SQS) go through `_socket` (proxied).

### dramatiq — COVERED TRANSITIVELY

Pure Python. Broker communication via `_socket` (proxied).

---

## Other Popular

### openai — COVERED TRANSITIVELY

Pure Python HTTP client built on httpx → httpcore → `_socket` + `_ssl`.

---

## Summary

### Needs proxying

| Library | Risk | Approach |
|---------|------|----------|
| **grpcio** | Critical | Syscall-level interposition (LD_PRELOAD/ptrace) or block |
| **uvloop** | Critical | Syscall-level interposition or block (easiest: just don't use it) |
| **cryptography** | High | Proxy OpenSSL `RAND_bytes` at cffi level |
| **psycopg3** | High | Proxy `psycopg.pq.PGconn` interface |
| **mysqlclient** | High | Proxy `MySQLdb._mysql` module |
| **torch** | High (if ML in scope) | Force seed + `use_deterministic_algorithms(True)` |
| **tensorflow** | High (if ML in scope) | Force seed + `enable_op_determinism()` |
| **lxml** | Medium | Wrap to force file-object path, or proxy at C level |
| **cx_Oracle** | Medium | Proxy C extension |
| **oracledb thick** | Medium | Proxy `oracledb.thick_impl` (thin mode is fine) |
| **confluent-kafka** | Medium | Proxy `librdkafka` wrapper |

### Partially covered

| Library | Gap |
|---------|-----|
| **gevent** | Event loop (libev/libuv) bypasses `select`; c-ares DNS resolver |
| **google-cloud-*** | Defaults to gRPC transport (force REST as workaround) |
| **protobuf** | Map field serialization order (force `deterministic=True`) |
| **Pillow** | `_imaging.map_buffer` does C-level `mmap()` |

### Covered transitively (no action needed)

requests, urllib3, httpx, httpcore, aiohttp, websockets, paramiko, pika, kafka-python, boto3/botocore, asyncpg, pymysql, pymongo, redis-py, aiomysql, aiosqlite, SQLAlchemy, numpy, pandas, scipy, scikit-learn, bcrypt, PyJWT, celery, kombu, dramatiq, openai, msgpack, orjson, ujson, pydantic, oracledb (thin mode)
