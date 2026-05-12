import sqlite3


def main():
    print("=== sqlite_transaction_edge_test ===", flush=True)
    connection = sqlite3.connect("transaction_demo.sqlite3")
    connection.execute("drop table if exists ledger")
    connection.execute(
        "create table ledger (id integer primary key, account text, amount integer)"
    )

    with connection:
        connection.executemany(
            "insert into ledger (account, amount) values (?, ?)",
            [("cash", 10), ("cash", 15), ("bank", 40)],
        )

    try:
        with connection:
            connection.execute(
                "insert into ledger (account, amount) values (?, ?)",
                ("cash", 999),
            )
            raise RuntimeError("rollback")
    except RuntimeError:
        pass

    cash_total = connection.execute(
        "select sum(amount) from ledger where account = ?",
        ("cash",),
    ).fetchone()[0]
    rows = connection.execute(
        "select account, amount from ledger order by id"
    ).fetchall()
    connection.close()

    assert cash_total == 25
    assert rows == [("cash", 10), ("cash", 15), ("bank", 40)]
    print(f"rows={rows}", flush=True)
    print("sqlite transaction edge ok", flush=True)


if __name__ == "__main__":
    main()
