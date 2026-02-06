# Docker Tests

Record/replay tests using Docker containers for consistent environments and network isolation.

## Quick Start

```bash
# Run all tests
python -m dockertests

# Run specific test
python -m dockertests postgres_test -v

# List available tests
python -m dockertests --list

# Run single test manually (for debugging)
python -m dockertests.runtest tests/postgres_test -v
```

**How it works:**
1. Pulls pre-built image from GHCR (`ghcr.io/.../retrace-test-base:latest`)
2. If pull fails, auto-falls back to `python:3.11-slim`
3. Containers auto-install missing dependencies via `install.sh`
4. Dependencies cached in volumes for fast subsequent runs

## How It Works

### Intelligent Image Selection

```bash
python -m dockertests postgres_test
```

**Automatic flow:**

1. **Try to pull** `ghcr.io/user/repo/retrace-test-base:latest`
   - Contains: Python 3.11 + `retrace` + common deps
   - Public image, no auth needed
   - ✅ Success → use it
   - ❌ Fail → fallback to `python:3.11-slim`

2. **Container starts** and runs `install.sh`:
   ```bash
   # Checks if deps already installed, installs if missing
   pip install -r /app/dockertests/base-requirements.txt  # Common
   pip install -r /app/test/requirements.txt              # Test-specific
   python -m retracesoftware.autoenable                   # Setup retrace.pth
   ```

3. **Volumes cache** installed packages:
   - `pip-cache:/root/.cache/pip` → pip cache
   - `site-packages:/usr/local/lib/python3.11/site-packages` → installed packages

### Performance

- **First run:** ~30-60s (image pull + pip install)
- **Subsequent runs:** ~5-10s (cached image + cached packages)
- **After dep changes:** Only installs new/changed packages

### Test Structure

Each test directory is mounted as `/app/test/` with:
- `test.py` - Test code
- `requirements.txt` - Test-specific deps (optional)
- `docker-compose.yml` - Service definitions
- Any other files needed by the test

## Directory Structure

```
dockertests/
├── base-requirements.txt          # Common deps (baked into GHCR image)
├── docker-compose.default.yml     # Default compose for simple tests
├── docker-compose.example.yml     # Example compose for complex tests
├── install.sh                     # Auto-install script (mounted into containers)
├── run.py                         # Test suite orchestrator
├── runtest.py                     # Single test runner (can run standalone)
├── images.py                      # Image pull/fallback logic
└── tests/
    └── my_test/
        ├── test.py                # Test code (required)
        ├── docker-compose.yml     # Optional (uses default if missing)
        └── requirements.txt       # Test-specific deps (optional)
```

**Architecture:**
- `run.py` - Discovers tests, pulls image once, runs each test via `runtest.py`
- `runtest.py` - Runs individual test (record + replay), can be used standalone
- `images.py` - Handles image pull with auto-fallback to `python:3.11-slim`

**Note:** `docker-compose.yml` is optional!
- If present: Uses custom configuration (for tests needing postgres, redis, etc.)
- If missing: Uses `docker-compose.default.yml` (simple record/replay)

## Creating a Test

### Simple Test (No Infrastructure Needed)

Most tests don't need external services. Just create `test.py`:

1. **Create test:**
   ```bash
   mkdir -p dockertests/tests/my_test
   cat > dockertests/tests/my_test/test.py << 'EOF'
   def test():
       print("Hello from test!")
       assert 1 + 1 == 2
   
   if __name__ == "__main__":
       test()
   EOF
   ```

2. **Add dependencies (optional):**
   ```bash
   echo "requests" > dockertests/tests/my_test/requirements.txt
   ```

3. **Run:**
   ```bash
   python -m dockertests my_test -v
   ```

**That's it!** The runner automatically uses a default `docker-compose.yml`.

---

### Complex Test (With Infrastructure)

For tests needing postgres, redis, etc., create a custom `docker-compose.yml`:

1. **Create test directory:**
   ```bash
   mkdir -p dockertests/tests/postgres_test
   ```

2. **Create `docker-compose.yml`:**
   ```yaml
   services:
     # Infrastructure
     postgres:
       image: postgres:15
       environment:
         POSTGRES_PASSWORD: test
         POSTGRES_DB: testdb

     # Record service
     record:
       image: ${TEST_IMAGE:-retrace-test-base:latest}
       depends_on:
         - postgres
       volumes:
         - ../../src:/app/src:ro
         - ../../dockertests/install.sh:/app/install.sh:ro
         - ../../dockertests/base-requirements.txt:/app/dockertests/base-requirements.txt:ro
         - .:/app/test:ro
         - ./recording:/recording:rw
         - pip-cache:/root/.cache/pip
         - site-packages:/usr/local/lib/python3.11/site-packages
       environment:
         PYTHONPATH: /app/src
         DATABASE_URL: postgres://postgres:test@postgres:5432/testdb
       command: bash -c "bash /app/install.sh && python -m retracesoftware --recording /recording -- /app/test/test.py"

    # Replay service (no infrastructure)
    replay:
      image: ${TEST_IMAGE:-retrace-test-base:latest}
      network_mode: none
      volumes:
        - ../../src:/app/src:ro
        - .:/app/test:ro
        - ./recording:/recording:ro
        - site-packages:/usr/local/lib/python3.11/site-packages:ro
      environment:
        PYTHONPATH: /app/src
      command: python -m retracesoftware --recording /recording

   volumes:
     pip-cache:
     site-packages:
   ```

3. **Create test:**
   ```bash
   cat > dockertests/tests/postgres_test/test.py << 'EOF'
   import os
   import psycopg2
   
   def test():
       conn = psycopg2.connect(os.environ['DATABASE_URL'])
       cursor = conn.cursor()
       cursor.execute("SELECT 1")
       assert cursor.fetchone()[0] == 1
       print("✓ Postgres test passed!")
   
   if __name__ == "__main__":
       test()
   EOF
   ```

4. **Add dependencies:**
   ```bash
   echo "psycopg2-binary" > dockertests/tests/postgres_test/requirements.txt
   ```

5. **Run:**
   ```bash
   python -m dockertests postgres_test -v
   ```

## Image Management

Base images are built by **CI** (GitHub Actions) and pushed to GHCR.

`run.py` automatically pulls the image on first run. No manual building needed!

**If image pull fails:**
- Automatically falls back to `python:3.11-slim`
- Containers auto-install deps (slightly slower first run)
- Everything still works!

## CI/CD Integration

GitHub Actions automatically builds and pushes base images to GHCR when `base-requirements.txt` changes.

**Workflow:** `.github/workflows/build-test-image.yml`
- Triggers on: changes to `base-requirements.txt`, manual dispatch
- Builds: `python:3.11-slim` + `base-requirements.txt` + retrace autoenable
- Pushes to: `ghcr.io/user/repo/retrace-test-base:latest` (public)

**Running tests in CI:**
```yaml
- name: Run tests
  run: python -m dockertests
```

Tests automatically pull the pre-built image. No credentials needed (public image).

## Cleanup

```bash
# Remove dangling images
python -m dockertests --cleanup
```

## Manual Test Execution

For debugging or running individual tests, use the standalone test runner:

```bash
# Run full test (record + replay)
python -m dockertests.runtest tests/postgres_test

# Run only record phase
python -m dockertests.runtest tests/postgres_test --record-only

# Run only replay phase (assumes recording exists)
python -m dockertests.runtest tests/postgres_test --replay-only

# Show the compose file being used
python -m dockertests.runtest tests/simple_test --show-compose

# Cleanup test resources
python -m dockertests.runtest tests/postgres_test --down

# Verbose output
python -m dockertests.runtest tests/postgres_test -v
```

**Features:**
- ✅ Automatically pulls/checks base image (same as main runner)
- ✅ Auto-fallback to `python:3.11-slim` if pull fails
- ✅ Supports custom and default docker-compose files
- ✅ Proper cleanup of generated files and Docker resources

**Use cases:**
- Debugging individual tests
- Re-running failed tests
- Inspecting generated docker-compose files
- Manual cleanup of test resources

## Requirements Management

- **`base-requirements.txt`** - Common deps installed in base image (retrace, etc.)
- **`tests/*/requirements.txt`** - Test-specific deps (installed at runtime by install.sh)

When you add/change requirements:
1. Update `base-requirements.txt` or test-specific `requirements.txt`
2. If base requirements changed, CI rebuilds base image
3. Test-specific requirements auto-installed at runtime
4. No manual intervention needed!

## Troubleshooting

**"Cannot connect to Docker"**
- Make sure Docker Desktop is running

**"Could not pull image"**
- No problem! Automatic fallback to `python:3.11-slim`
- Containers will install all deps (slightly slower first time)
- Check GHCR: https://github.com/user/repo/pkgs/container/retrace-test-base

**Slow first run**
- Image pull: ~1-2 minutes (one-time, cached locally)
- Pip install: ~30-60s (cached in volumes)
- Subsequent runs: ~5-10s

**Tests fail in replay**
- Check if test code is deterministic
- Verify recording directory has content
- Run with `-v` for verbose output

**Force refresh**
- Clear image cache: `python -m dockertests --cleanup`
- Or manually: `docker rmi retrace-test-base:latest`
- Next run pulls fresh from GHCR
