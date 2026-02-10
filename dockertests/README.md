# Docker Tests

Record/replay tests using Docker containers for consistent environments and network isolation.

## Quick Start

```bash
# Run all tests
python run.py

# Run specific test
python run.py postgres_test

# List available tests
python run.py --list

# Run single test manually (for debugging)
./runtest.sh postgres_test
```
The harness runs each test through `install -> dryrun -> record -> replay -> cleanup`.
On failure, `runtest.sh` reports the failed phase and prints service logs.

## How It Works

### Dependency install and caching

Each run uses the selected image (default `python:3.11-slim`) and executes `install.sh`:
   ```bash
   pip install -r /app/dockertests/base-requirements.txt  # Common
   pip install -r /app/test/requirements.txt              # Test-specific
   ```

Package installs are isolated per test and image under:
- `./.cache/packages/<test_name>/<image_tag>/`
- `./.cache/packages-debug/<test_name>/<image_tag>/` (debug mode)

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
├── base-requirements.txt
├── docker-compose.base.yml
├── docker-compose.server-base.yml
├── install.sh
├── run.py
├── runtest.sh
└── tests/
    └── my_test/
        ├── test.py
        ├── docker-compose.yml      # Optional override
        ├── requirements.txt        # Optional
        └── tags                    # Optional
```

**Architecture:**
- `run.py` - Discovers/tests filters and invokes `runtest.sh` per test
- `runtest.sh` - Runs one test pipeline and reports failed phase/logs
- `docker-compose.base.yml` / `docker-compose.server-base.yml` - Base workflows

**Note:** `docker-compose.yml` is optional!
- If present: merged as an override (for postgres, flask, perf, etc.)
- If missing: base compose handles install/dryrun/record/replay/cleanup

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
   python run.py my_test
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
   python run.py postgres_test
   ```

## CI/CD Integration

GitHub Actions automatically builds and pushes base images to GHCR when `base-requirements.txt` changes.

**Workflow:** `.github/workflows/build-test-image.yml`
- Triggers on: changes to `base-requirements.txt`, manual dispatch
- Builds: `python:3.11-slim` + `base-requirements.txt` + retrace autoenable
- Pushes to: `ghcr.io/user/repo/retrace-test-base:latest` (public)

**Running tests in CI:**
```yaml
- name: Run tests
  run: python run.py
```

## Manual Test Execution

For debugging or running individual tests, use `runtest.sh`:

```bash
# Run full test (record + replay)
./runtest.sh postgres_test

# Override image
./runtest.sh postgres_test --image python:3.11-slim

# Debug mode (record under gdb)
./runtest.sh postgres_test --debug
```

**Features:**
- ✅ Uses the same pipeline as `run.py`
- ✅ Reports failed phase (`dryrun`, `record`, `replay`, etc.) on error
- ✅ Supports custom compose overrides and image selection

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
- Use `--image` to pick a known-good image:
  - `python run.py --image python:3.11-slim`
  - `./runtest.sh <test_name> --image python:3.11-slim`

**Slow first run**
- Image pull: ~1-2 minutes (one-time, cached locally)
- Pip install: ~30-60s (cached in volumes)
- Subsequent runs: ~5-10s

**Tests fail in replay**
- Check if test code is deterministic
- Verify recording directory has content
- Run with `-v` for verbose output

**Force refresh**
- Remove cached package installs:
  - `rm -rf .cache/packages .cache/packages-debug`
- Re-run the test to reinstall dependencies
