import os
import time

import pandas as pd
import pyodbc
import sqlalchemy


def _connection_string(database: str | None = None) -> str:
    return (
        f"DRIVER={{{os.environ['DB_DRIVER']}}};"
        f"SERVER={os.environ['DB_HOST']},{os.environ['DB_PORT']};"
        f"DATABASE={database or os.environ['DB_NAME']};"
        f"UID={os.environ['DB_USER']};"
        f"PWD={os.environ['DB_PASSWORD']};"
        "Encrypt=no;"
        "TrustServerCertificate=yes"
    )


def _connect(database: str | None = None):
    last_error = None
    for _ in range(30):
        try:
            return pyodbc.connect(_connection_string(database), timeout=3)
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"could not connect to SQL Server: {last_error}") from last_error


def _engine(database: str | None = None) -> sqlalchemy.Engine:
    url = sqlalchemy.engine.URL.create(
        "mssql+pyodbc",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=database or os.environ["DB_NAME"],
        query={
            "driver": os.environ["DB_DRIVER"],
            "Encrypt": "no",
            "TrustServerCertificate": "yes",
        },
    )
    return sqlalchemy.create_engine(url)


def test_direct_pyodbc_query():
    connection = _connect()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT CAST(123 AS INT) AS value")
        row = cursor.fetchone()
        assert row[0] == 123
        print("pyodbc row:", row[0], flush=True)
    finally:
        connection.close()


def test_sqlalchemy_pandas_read_sql_query():
    engine = _engine()
    with engine.connect() as connection:
        frame = pd.read_sql(
            sqlalchemy.text("SELECT CAST(456 AS INT) AS value"),
            connection,
        )
    assert frame.to_dict("records") == [{"value": 456}]
    print("pandas row:", frame.to_dict("records")[0]["value"], flush=True)


def test():
    print("=== pyodbc_mssql_test ===", flush=True)
    test_direct_pyodbc_query()
    test_sqlalchemy_pandas_read_sql_query()
    print("pyodbc mssql record/replay scenario ok", flush=True)


if __name__ == "__main__":
    test()
