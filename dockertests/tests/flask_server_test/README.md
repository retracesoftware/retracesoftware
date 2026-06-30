# Flask Server Test (SERVER-SIDE TRACING)

**Key Difference:** Retrace runs on the **Flask server**, not the client!

This tests server-side logic, database operations, and business logic.

## File Structure

- **`test.py`** ← The Flask server being tested (runs under retrace)
- **`client.py`** ← HTTP client that generates load (plain Python, no retrace)

**Convention:** `test.py` is the application/server being tested, not a test client!

## Client vs Server Testing

### flask_test/ (Client-side tracing) ❌
```
Client ──HTTP──> Flask Server
  ↑                    ↓
Retrace            Database
Records HTTP    (real operations)
requests
```
- Records HTTP requests/responses
- Flask runs normally
- Database operations happen for real

### flask_server_test/ (Server-side tracing) ✅
```
Client ──HTTP──> Flask Server
                      ↑        ↓
                  Retrace  Database
                  Records DB ops
```
- Flask runs **under retrace**
- Records database operations, file I/O, etc.
- Tests actual server implementation

---

## What Gets Tested

**`test.py`** (the Flask server) performs actual operations:
- ✅ SQLite database queries (INSERT, SELECT)
- ✅ Database transactions
- ✅ Business logic in route handlers
- ✅ Error handling and validation
- ✅ State management

**`client.py`** (the load generator) simply makes HTTP requests.

During replay:
- ❌ No real database operations
- ✅ All responses come from recording
- ✅ Server logic re-executed deterministically
- ✅ Client makes same requests, gets same responses

---

## Architecture

### Record Phase:
```
┌─────────────────┐         ┌──────────────────────────┐
│   client.py     │  HTTP   │  flask-record            │
│  (load gen      │ ──────> │  ┌─────────────────────┐ │
│   container)    │ <────── │  │  test.py (Flask)   │ │
└─────────────────┘         │  │  under retrace      │ │
                            │  └──────────┬──────────┘ │
                            │             │            │
                            │       Records DB ops     │
                            │             ↓            │
                            │       ┌──────────┐       │
                            │       │ SQLite   │       │
                            │       └──────────┘       │
                            └──────────────────────────┘
```

1. Flask server container (`test.py`) starts under retrace
2. Health check waits for Flask to be ready
3. Client container (`client.py`) starts
4. Client makes HTTP requests to `http://flask-record:5000`
5. Flask processes requests → **database operations recorded**
6. Responses sent back to client

**Recorded:** Database I/O, file operations, timestamps

### Replay Phase:
```
┌──────────────────────────┐
│  replay                  │
│  ┌─────────────────────┐ │
│  │  test.py (Flask)   │ │
│  │  under retrace     │ │
│  └──────────┬──────────┘ │
│             │            │
│      Replay from rec     │
│             ↓            │
│       ┌──────────┐       │
│       │ no real  │       │
│       │ database │       │
│       │ or client│       │
│       └──────────┘       │
└──────────────────────────┘
```

1. The replay service runs the extracted recording for `test.py`.
2. The Flask server code re-executes under retrace replay mode.
3. Recorded socket, request, response, database, and file operations are served
   from the trace.
4. **No live client or database is required.**

---

## Files

- **`test.py`** - Flask server with SQLite database (THE TEST - runs under retrace)
- **`client.py`** - Simple HTTP client (load generator, runs in separate container)
- `docker-compose.yml` - Multi-container orchestration
- `requirements.txt` - flask, requests

**Key Convention:** `test.py` is the application being tested (Flask server), not a test client!

---

## Why Separate Containers?

During record, separate containers keep the Flask server and test client
lifecycles clean:

✅ **More realistic** - mirrors production architecture  
✅ **Clean separation** - server and client are isolated  
✅ **True network testing** - tests actual HTTP over network  
✅ **Better observability** - can inspect each container independently  
✅ **Flexible** - easy to add more clients or services  

```yaml
# Record phase
flask-record:      # Flask under retrace
  environment:
    RETRACE_RECORDING: /recording/trace.bin
  command: bash -c "python -m retracesoftware enable-hook && python /app/test/test.py"
  
record:            # Test client (plain Python)
  depends_on:
    flask-record:
      condition: service_healthy
  command: python /app/client.py
```

## Running

```bash
# Via test runner
./runtest.sh flask_server_test

# Or main runner
python run.py flask_server_test

# Manual docker-compose (record phase)
cd dockertests/tests/flask_server_test
TEST_IMAGE=retracesoftware-test docker-compose up --abort-on-container-exit record

# Manual docker-compose (replay phase)
TEST_IMAGE=retracesoftware-test docker-compose up --abort-on-container-exit replay
```

---

## Expected Output

```
Starting Flask server under retrace...
Waiting for Flask to start...
✓ Flask is ready!
Running test client...
============================================================
Flask Server Test - Load Generator
============================================================
Waiting for Flask server at http://localhost:5000...
✓ Flask server is ready!

📝 Creating users...
  ✓ Created user 1: User 1
  ✓ Created user 2: User 2
  ✓ Created user 3: User 3

📋 Fetching all users...
  ✓ Found 3 users

📝 Creating posts...
  ✓ Created post 1: Post 1 by User 1
  ✓ Created post 2: Post 2 by User 1
  ✓ Created post 3: Post 1 by User 2
  ✓ Created post 4: Post 2 by User 2
  ✓ Created post 5: Post 1 by User 3
  ✓ Created post 6: Post 2 by User 3

📖 Fetching user posts...
  ✓ User User 1 has 2 posts
  ✓ User User 2 has 2 posts
  ✓ User User 3 has 2 posts

📊 Getting stats...
  ✓ Total users: 3
  ✓ Total posts: 6

============================================================
✅ All operations completed successfully!
============================================================
```

---

## What's Being Recorded

During the record phase, retrace captures **all I/O from test.py**:

1. **Database Operations:**
   - `sqlite3.connect('/tmp/app.db')`
   - `cursor.execute('INSERT INTO users ...')`
   - `cursor.fetchall()`
   - `conn.commit()`

2. **File I/O:**
   - Database file reads/writes
   - SQLite journal files

3. **System Calls:**
   - Timestamps from `time.time()`
   - Random number generation (if used)

During replay, `test.py` runs but **none of these operations actually happen** - they're all replayed from the recording!

---

## Why This Is Useful

### Traditional Testing:
```python
# Mock the database in test.py
@mock.patch('test.get_db')
def test_create_user(mock_db):
    mock_db.return_value = fake_connection
    # ...
```

### With Retrace:
```python
# test.py - NO mocking needed!
# Just write normal Flask code
@app.route('/api/users', methods=['POST'])
def create_user():
    conn = get_db()  # Real DB during record, replayed during replay
    cursor = conn.execute('INSERT INTO users ...')
    return jsonify(result)
```

The **real Flask code** runs in both record and replay. No mocks, no stubs!

---

## Comparing to Client-side Test

Both tests use **separate containers** for client and server. The key difference is **where retrace runs**:

| Aspect | flask_test (client) | flask_server_test (server) |
|--------|---------------------|----------------------------|
| **Retrace runs on** | Test client container | Flask server container |
| **Records** | HTTP requests/responses | Database operations, file I/O |
| **Tests** | API contract, HTTP behavior | Server implementation, business logic |
| **Replay needs Flask?** | ❌ No (HTTP from recording) | ✅ Yes (server code re-executes under replay) |
| **Replay needs DB?** | ❌ No (HTTP from recording) | ❌ No (DB ops from recording) |
| **Use case** | End-to-end API testing | Server-side logic testing |

### Example:

**flask_test (client-side):**
```yaml
record:  # Test client UNDER retrace
  environment:
    RETRACE_RECORDING: /recording/trace.bin
  command: bash -c "python -m retracesoftware enable-hook && python /app/test/test.py"

flask:   # Normal Flask server
  command: python app.py
```

**flask_server_test (server-side):**
```yaml
flask-record:  # test.py (Flask) UNDER retrace
  environment:
    RETRACE_RECORDING: /recording/trace.bin
  command: bash -c "python -m retracesoftware enable-hook && python /app/test/test.py"

record:  # client.py (load generator)
  command: python /app/client.py
```

---

## Key Insight

The Flask server code **actually runs** during replay, but all I/O (database, file system, network) comes from the recording. This means:

✅ You test real business logic  
✅ No mocks needed  
✅ Deterministic results  
✅ Fast replays (no real I/O)  

Perfect for testing complex server-side logic with external dependencies!
