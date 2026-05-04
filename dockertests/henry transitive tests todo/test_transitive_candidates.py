"""Record/replay smoke tests for transitive compatibility candidates."""

from __future__ import annotations

from pathlib import Path
import os
import re
import subprocess
import sys
import textwrap

import pytest


TIMEOUT = 30


def _run_record(script_path: Path, recording: Path, env: dict[str, str]):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            str(script_path),
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
    )


def _run_replay(recording: Path, env: dict[str, str]):
    return subprocess.run(
        [sys.executable, "-m", "retracesoftware", "--recording", str(recording)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        env=env,
    )


CASES = [
    ("apispec", "apispec", "from apispec import APISpec\nspec = APISpec(title='Demo', version='1.0', openapi_version='3.0.3')\nprint(spec.to_dict()['info']['title'])"),
    ("ariadne", "ariadne", "from ariadne import QueryType, gql, make_executable_schema\nfrom graphql import graphql_sync\ntype_defs = gql('type Query { hello: String! }')\nquery = QueryType()\n@query.field('hello')\ndef resolve_hello(*_):\n    return 'world'\nschema = make_executable_schema(type_defs, query)\nprint(graphql_sync(schema, '{ hello }').data['hello'])"),
    ("attrs", "attr", "import attr\n@attr.s\nclass Point:\n    x = attr.ib()\n    y = attr.ib()\nprint(attr.asdict(Point(2, 3))['y'])"),
    ("blacken-docs", "blacken_docs", "import blacken_docs\nprint(blacken_docs.__name__)"),
    ("boltons", "boltons", "from boltons.iterutils import chunked\nprint(list(chunked([1, 2, 3, 4], 2))[1][0])"),
    ("build", "build", "from build.env import DefaultIsolatedEnv\nprint(DefaultIsolatedEnv.__name__)"),
    ("cattrs", "cattrs", "import attr\nimport cattrs\n@attr.s\nclass Point:\n    x = attr.ib()\nprint(cattrs.unstructure(Point(7))['x'])"),
    ("click-option-group", "click_option_group", "from click_option_group import MutuallyExclusiveOptionGroup\nprint(MutuallyExclusiveOptionGroup.__name__)"),
    ("click-plugins", "click_plugins", "import click\nfrom click_plugins import with_plugins\n@click.command('demo')\ndef demo():\n    pass\n@with_plugins([demo])\n@click.group()\ndef cli():\n    pass\nprint('demo' in cli.commands)"),
    ("dataclasses-json", "dataclasses_json", "from dataclasses import dataclass\nfrom dataclasses_json import dataclass_json\n@dataclass_json\n@dataclass\nclass Item:\n    name: str\nprint(Item.from_json('{\"name\": \"Ada\"}').name)"),
    ("dateparser", "dateparser", "import dateparser\nprint(dateparser.parse('2020-01-02').date().isoformat())"),
    ("dynaconf", "dynaconf", "from dynaconf import Dynaconf\nsettings = Dynaconf(environments=False, FOO='bar')\nprint(settings.FOO)"),
    ("fastapi-utils", "fastapi_utils", "from fastapi_utils.cbv import cbv\nprint(cbv.__name__)"),
    ("flake8", "flake8", "import flake8\nprint(flake8.__name__)"),
    ("flask-cors", "flask_cors", "from flask import Flask\nfrom flask_cors import CORS\napp = Flask(__name__)\nCORS(app)\nprint('flask-cors')"),
    ("flask-login", "flask_login", "from flask import Flask\nfrom flask_login import LoginManager\napp = Flask(__name__)\nmanager = LoginManager(app)\nprint(manager.login_view)"),
    ("flask-wtf", "flask_wtf", "from flask_wtf import FlaskForm\nprint(FlaskForm.__name__)"),
    ("freezegun", "freezegun", "from datetime import datetime\nfrom freezegun import freeze_time\nwith freeze_time('2024-01-02 03:04:05'):\n    print(datetime.now().isoformat())"),
    ("graphql-core", "graphql", "from graphql import build_schema, graphql_sync\nschema = build_schema('type Query { hello: String }')\nresult = graphql_sync(schema, '{ hello }', root_value={'hello': 'world'})\nprint(result.data['hello'])"),
    ("humanize", "humanize", "import humanize\nprint(humanize.naturalsize(1024))"),
    ("isort", "isort", "from isort import code\nprint(code('import sys\\nimport os\\n').splitlines()[0])"),
    ("itsdangerous", "itsdangerous", "from itsdangerous import URLSafeSerializer\nserializer = URLSafeSerializer('secret')\ntoken = serializer.dumps({'name': 'Ada'})\nprint(serializer.loads(token)['name'])"),
    ("jinja2", "jinja2", "from jinja2 import Template\nprint(Template('hello {{ name }}').render(name='Ada'))"),
    ("jsonpickle", "jsonpickle", "import jsonpickle\npayload = jsonpickle.encode({'name': 'Ada'})\nprint(jsonpickle.decode(payload)['name'])"),
    ("loguru", "loguru", "from loguru import logger\nlogger.disable('__main__')\nprint(logger.level('INFO').name)"),
    ("marshmallow", "marshmallow", "from marshmallow import Schema, fields\nclass UserSchema(Schema):\n    name = fields.Str(required=True)\nprint(UserSchema().load({'name': 'Ada'})['name'])"),
    ("more-itertools", "more_itertools", "from more_itertools import first\nprint(first([3, 4, 5]))"),
    ("nox", "nox", "import nox\nprint(hasattr(nox, 'session'))"),
    ("opentelemetry-instrumentation-requests", "opentelemetry.instrumentation.requests", "from opentelemetry.instrumentation.requests import RequestsInstrumentor\nprint(RequestsInstrumentor.__name__)"),
    ("platformdirs", "platformdirs", "from platformdirs import user_cache_dir\nprint(user_cache_dir('demo-app', 'demo-author').split('/')[-1])"),
    ("pluggy", "pluggy", "import pluggy\npm = pluggy.PluginManager('demo')\nprint(pm.project_name)"),
    ("pre-commit", "pre_commit", "import pre_commit\nprint(pre_commit.__name__)"),
    ("pycodestyle", "pycodestyle", "import pycodestyle\nstyle = pycodestyle.StyleGuide(quiet=True)\nprint(style.check_files([]).total_errors)"),
    ("pyflakes", "pyflakes", "from io import StringIO\nfrom pyflakes.api import check\nfrom pyflakes.reporter import Reporter\nout = StringIO()\nerr = StringIO()\nprint(check('x = 1\\n', 'snippet.py', Reporter(out, err)))"),
    ("pytest-asyncio", "pytest_asyncio", "import pytest_asyncio\nprint(pytest_asyncio.__name__)"),
    ("pytest-httpx", "pytest_httpx", "from pytest_httpx import HTTPXMock\nprint(HTTPXMock.__name__)"),
    ("pytest-mock", "pytest_mock", "import pytest_mock\nprint(pytest_mock.__name__)"),
    ("python-dotenv", "dotenv", "from io import StringIO\nfrom dotenv import dotenv_values\nprint(dotenv_values(stream=StringIO('NAME=Ada\\n'))['NAME'])"),
    ("requests-cache", "requests_cache", "import requests_cache\nsession = requests_cache.CachedSession(backend='memory')\nprint(session.cache.__class__.__name__)"),
    ("requests-oauthlib", "requests_oauthlib", "from requests_oauthlib import OAuth1\nauth = OAuth1('client-key', 'client-secret')\nprint(auth.client.client_key)"),
    ("requests-toolbelt", "requests_toolbelt", "from requests_toolbelt.multipart.encoder import MultipartEncoder\nencoder = MultipartEncoder(fields={'name': 'Ada'})\nprint('multipart/form-data' in encoder.content_type)"),
    ("responses", "responses", "import requests\nimport responses\nwith responses.RequestsMock() as mocked:\n    mocked.add(responses.GET, 'http://example.test/', json={'ok': True})\n    print(requests.get('http://example.test/').json()['ok'])"),
    ("respx", "respx", "import httpx\nimport respx\nwith respx.mock:\n    respx.get('https://example.test/').mock(return_value=httpx.Response(200, json={'ok': True}))\n    print(httpx.get('https://example.test/').json()['ok'])"),
    ("rich-click", "rich_click", "import rich_click as click\n@click.command()\ndef cli():\n    pass\nprint(cli.name)"),
    ("slowapi", "slowapi", "from slowapi import Limiter\nfrom slowapi.util import get_remote_address\nlimiter = Limiter(key_func=get_remote_address)\nprint(limiter.enabled)"),
    ("sortedcontainers", "sortedcontainers", "from sortedcontainers import SortedList\nprint(list(SortedList([3, 1, 2]))[0])"),
    ("strawberry-graphql", "strawberry", "import strawberry\n@strawberry.type\nclass Query:\n    @strawberry.field\n    def hello(self) -> str:\n        return 'world'\nschema = strawberry.Schema(query=Query)\nprint(schema.execute_sync('{ hello }').data['hello'])"),
    ("structlog", "structlog", "import structlog\nlogger = structlog.get_logger('demo').bind(answer=42)\nprint(logger.__class__.__name__)"),
    ("tenacity", "tenacity", "from tenacity import retry, stop_after_attempt\n@retry(stop=stop_after_attempt(1))\ndef work():\n    return 'ok'\nprint(work())"),
    ("tomli", "tomli", "import tomli\nprint(tomli.loads(\"name = 'Ada'\")['name'])"),
    ("tomli-w", "tomli_w", "import tomli_w\nprint('name' in tomli_w.dumps({'name': 'Ada'}))"),
    ("toolz", "toolz", "from toolz import compose\nprint(compose(lambda x: x + 1, lambda x: x * 2)(3))"),
    ("tox", "tox", "import tox\nprint(tox.__name__)"),
    ("twine", "twine", "import twine\nprint(twine.__name__)"),
    ("typer", "typer", "import typer\napp = typer.Typer()\n@app.command()\ndef hello(name: str = 'Ada'):\n    print(name)\nprint(app.info.name)"),
    ("vcrpy", "vcr", "import vcr\nrecorder = vcr.VCR()\nprint(recorder.path_transformer is None)"),
    ("virtualenv", "virtualenv", "from virtualenv.discovery.py_info import PythonInfo\nprint(PythonInfo.__name__)"),
    ("wtforms", "wtforms", "from wtforms import Form, StringField\nclass NameForm(Form):\n    name = StringField()\nprint(NameForm(data={'name': 'Ada'}).name.data)"),
]


def _safe_name(package: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", package)


@pytest.mark.parametrize("package, import_name, script", CASES, ids=[case[0] for case in CASES])
def test_transitive_candidate_records_and_replays(tmp_path: Path, package: str, import_name: str, script: str):
    pytest.importorskip(import_name)

    script_path = tmp_path / f"{_safe_name(package)}_smoke.py"
    script_path.write_text(
        "from __future__ import annotations\n" + textwrap.dedent(script).strip() + "\n",
        encoding="utf-8",
    )
    recording = tmp_path / f"{_safe_name(package)}.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    record = _run_record(script_path, recording, env)
    assert record.returncode == 0, (
        f"record failed for transitive candidate {package}\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = _run_replay(recording, env)
    assert replay.returncode == 0, (
        f"replay failed for transitive candidate {package}\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
