from django.conf import settings


def configure_django():
    if settings.configured:
        return
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": "django_orm_demo.sqlite3",
            }
        },
        INSTALLED_APPS=[],
        SECRET_KEY="dockertest-secret",
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
        USE_TZ=True,
    )


def main():
    print("=== django_orm_sqlite_test ===", flush=True)
    configure_django()

    import django
    from django.db import connection, models, transaction

    django.setup()

    class Ticket(models.Model):
        customer = models.CharField(max_length=64)
        destination = models.CharField(max_length=64)
        price = models.IntegerField()

        class Meta:
            app_label = "dockertest"

    with connection.schema_editor() as schema_editor:
        existing = connection.introspection.table_names()
        if Ticket._meta.db_table in existing:
            schema_editor.delete_model(Ticket)
        schema_editor.create_model(Ticket)

    Ticket.objects.bulk_create(
        [
            Ticket(customer="Ada", destination="Boston", price=320),
            Ticket(customer="Grace", destination="London", price=480),
            Ticket(customer="Linus", destination="Boston", price=310),
        ]
    )

    boston = list(
        Ticket.objects.filter(destination="Boston")
        .order_by("price")
        .values_list("customer", "price")
    )
    assert boston == [("Linus", 310), ("Ada", 320)]
    print(f"boston={boston}", flush=True)

    try:
        with transaction.atomic():
            Ticket.objects.create(customer="Temp", destination="Paris", price=999)
            raise RuntimeError("rollback")
    except RuntimeError:
        pass

    assert Ticket.objects.count() == 3
    print("django orm sqlite ok", flush=True)


if __name__ == "__main__":
    main()
