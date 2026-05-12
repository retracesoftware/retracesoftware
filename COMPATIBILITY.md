# Compatibility

This document lists the libraries Retrace has been exercised against in
development and CI. It is updated as coverage expands.

## What "tested" means

A library appears here when Retrace correctly records and deterministically
replays Python programs that use it under our test suite. This is a stronger
claim than "imports cleanly" and a weaker claim than "every API surface
verified." Known caveats are noted inline.

If you hit a problem with a library listed here, please open an issue with a
minimal reproducer, Python version, and OS.

## Current caveats

- Threading support is currently experimental, with full support being released
  in the next version. Under certain scenarios threading diverges; see
  [threading issues](https://github.com/retracesoftware/retracesoftware/issues?q=is%3Aissue%20is%3Aopen%20threading).

## Python versions

[TODO: 3.x, 3.y]

## Operating systems

[TODO: Linux x86_64, macOS arm64, ...]

---

## Runtime boundaries

These are the standard-library surfaces Retrace instruments directly. Correct
record and replay across them is the foundation everything else depends on.
Most other tools approximate these with hooks or sampling; Retrace captures the
full causal sequence.

- **Threading and synchronisation:** `threading`, `queue`,
  `concurrent.futures`, `weakref` finalizers (experimental; see
  [current caveats](#current-caveats))
- **Networking:** `socket`, `select`, `ssl`
- **Process and IPC:** `subprocess`, `_posixsubprocess`, `multiprocessing`,
  `billiard`
- **Filesystem and paths:** `pathlib`
- **Time and randomness:** `time`, `random`
- **Database:** `sqlite3` (`_sqlite3`)
- **Module loading:** `import`, `runpy`
- **Compression:** `lz4`

## Async runtime

- `asyncio`
- `anyio`, `asgiref`
- `uvicorn`
- `aiosignal`, `aiorwlock`, `async-lru`

## Web frameworks

- Flask, Django, FastAPI, Starlette, Wagtail
- Datasette / Uvicorn
- Django REST Framework, slowapi, fastapi-utils
- ariadne, strawberry-graphql, graphql-core

## Web framework extensions

- Werkzeug
- django-filter, django-modelcluster, django-taggit, django-treebeard
- flask-cors, flask-login, flask-wtf, wtforms

## HTTP clients

- requests, httpx, httpcore, aiohttp
- requests streaming responses
- httpx mock transports
- aiohttp-cors, requests-cache, requests-oauthlib, requests-toolbelt

### HTTP test doubles and recorders

- vcrpy, responses, respx

## Databases

- **SQLite** via `sqlite3`
- **PostgreSQL** via psycopg2
- **SQLAlchemy** with SQLite
- **Django ORM** with SQLite

> asyncpg and aiopg are on the near-term roadmap. See
> [Not yet tested](#not-yet-tested).

## Cache / Redis

- Redis client APIs through redis-py / fakeredis

## Cloud SDKs

- boto3 / botocore with `Stubber`-backed S3 clients

## Data validation and serialisation

- Pydantic, marshmallow, attrs, cattrs
- jsonschema, dataclasses-json, jsonpickle
- apischema, apispec, coreapi

## Templating

- Jinja2, Werkzeug routing

## Scientific and data

- NumPy, Pandas, SciPy

## LLM and model boundaries

- HuggingFace Hub boundary calls
- llama-cpp model boundary calls

## Observability

- OpenTelemetry API and SDK, `opentelemetry-instrumentation-requests`
- opencensus
- structlog, loguru

## Security and cryptography

- `cryptography`, pyOpenSSL
- itsdangerous

## CLI tooling

- Click, Typer, Rich
- rich-click, click-option-group, click-plugins

## Configuration and environment

- dynaconf, python-dotenv
- platformdirs, appdirs
- filelock

## Date and time

- python-dateutil, pytz, arrow, dateparser, Babel
- freezegun

## Resilience and retry

- backoff, tenacity, cachecontrol

## Filesystem

- `pathlib`, fsspec

## Process and system

- psutil, appnope

## Networking extras

- gRPC, protobuf

## Packaging and runtime

- packaging, tomli, tomli-w

## General utilities

- boltons, more-itertools, sortedcontainers, toolz
- humanize, astroid, asttokens, bytecode

---

## Test infrastructure

These are exercised by Retrace's own test suite and CI, and are relevant if you
are integrating Retrace into a test harness:

- pytest, pytest-asyncio, pytest-httpx, pytest-mock
- freezegun (interacts with Retrace's time determinism)
- pluggy, execnet

## Development environment

Linters, formatters, and packaging tools used during Retrace development.
Listed for reproducibility, not as record/replay compatibility claims:

- Black, isort, flake8, pycodestyle, pyflakes, blacken-docs, pre-commit
- tox, nox, build, twine, virtualenv

---

## Not yet tested

Libraries with active user demand that we have not yet validated. Listed openly
so you can see exactly where coverage stops:

- asyncpg, aiopg, aiomysql
- Live Redis server coverage beyond redis-py / fakeredis
- aiobotocore
- Celery, kombu
- Kafka clients (kafka-python, aiokafka, confluent-kafka)

If you need one of these, open an issue with your stack details. Prioritisation
follows demand.

## Reporting compatibility

- A library works with Retrace and is not listed: open a PR adding it here, or
  an issue with a minimal example.
- A listed library breaks: open an issue with a reproducer, Python version, and
  OS.
