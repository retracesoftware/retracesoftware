from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select


def main():
    print("=== sqlalchemy_sqlite_test ===", flush=True)
    engine = create_engine("sqlite:///sqlalchemy_demo.db", future=True)
    metadata = MetaData()
    users = Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String, nullable=False),
        Column("role", String, nullable=False),
    )

    metadata.drop_all(engine)
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            users.insert(),
            [
                {"name": "Ada", "role": "engineer"},
                {"name": "Grace", "role": "scientist"},
                {"name": "Linus", "role": "engineer"},
            ],
        )

    with engine.connect() as connection:
        rows = connection.execute(
            select(users.c.name).where(users.c.role == "engineer").order_by(users.c.id)
        ).scalars().all()

    assert rows == ["Ada", "Linus"]
    print(f"engineers={rows}", flush=True)

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            users.insert(),
            {"name": "Rollback", "role": "temporary"},
        )
        transaction.rollback()
        count = connection.execute(select(users.c.id)).all()

    assert len(count) == 3
    print("sqlalchemy sqlite ok", flush=True)


if __name__ == "__main__":
    main()
