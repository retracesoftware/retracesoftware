# Flask Load Test

This test demonstrates record/replay of HTTP requests to a Flask web server.

## What It Tests

- **Flask Server:** Simple REST API with multiple endpoints
- **Load Generation:** Test client makes HTTP requests (GET, POST)
- **Stateful Testing:** Counter endpoint tests state preservation
- **Concurrent Requests:** Multiple requests to test load handling

## Architecture

```
┌─────────────────────┐
│   Flask Service     │  (only in record phase)
│   http://flask:5000 │
│   - GET /           │
│   - GET /api/users  │
│   - POST /api/users │
│   - GET /api/count  │
└──────────┬──────────┘
           │ HTTP Requests
           │
    ┌──────▼────────┐
    │  Test Client  │
    │  (retrace)    │
    │  test.py      │
    └───────────────┘
```

### Record Phase
1. Flask server starts in a container
2. Test client waits for Flask to be healthy
3. Test client makes HTTP requests (recorded by retrace)
4. All request/response data captured

### Replay Phase
1. **No Flask server** (network disabled)
2. Test client runs with same logic
3. All HTTP responses come from recording
4. Validates deterministic replay

## Files

- `app.py` - Flask server application
- `test.py` - Test client that generates load
- `docker-compose.yml` - Multi-service setup
- `requirements.txt` - Test dependencies (flask, requests)

## Running

```bash
# Run via test runner
python -m dockertests.runtest tests/flask_test -v

# Or via main runner
python -m dockertests flask_test

# Manual docker-compose
cd dockertests/tests/flask_test
TEST_IMAGE=python:3.11-slim docker-compose up --abort-on-container-exit record
TEST_IMAGE=python:3.11-slim docker-compose up --abort-on-container-exit replay
```

## Expected Output

```
Waiting for Flask server at http://flask:5000...
✓ Flask server is ready!

📍 Testing GET /
✓ Home endpoint returned: Hello from Flask!

📍 Testing GET /api/users/42
✓ User endpoint returned: {'id': 42, 'name': 'User 42', ...}

📍 Testing POST /api/users
✓ Create user returned: {'id': 123, 'created': True, ...}

📍 Testing stateful counter /api/count
  Request 1: count = 1
  Request 2: count = 2
  Request 3: count = 3
  Request 4: count = 4
  Request 5: count = 5
✓ Counter incremented correctly

📍 Testing concurrent load (10 requests)
✓ Completed 10/10 requests in 0.15s

✅ All Flask tests passed!
```

## Key Features

### Stateful Testing
The `/api/count` endpoint maintains state (increments counter). This tests that retrace properly captures and replays stateful interactions.

### Health Checks
The docker-compose file uses health checks to ensure Flask is ready before the test starts:
```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:5000/health')"]
  interval: 2s
  retries: 10
```

### Network Isolation
During replay, `network_mode: none` ensures no actual network calls are made - everything comes from the recording.

## Customization

Want to add more endpoints? Edit `app.py`:

```python
@app.route('/api/custom')
def custom():
    return jsonify({'result': 'custom data'})
```

Want more load? Edit `test.py`:

```python
def test_heavy_load():
    for i in range(100):  # 100 requests
        requests.get(f"{BASE_URL}/api/users/{i}")
```
