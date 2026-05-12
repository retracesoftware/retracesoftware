from celery import Celery
from kombu import Connection


app = Celery("retrace_celery_kombu", broker="memory://", backend="cache+memory://")
app.conf.update(task_always_eager=True, task_store_eager_result=True)


@app.task(name="retrace.add")
def add(left, right):
    return left + right


@app.task(name="retrace.describe")
def describe(name, score):
    return {"name": name, "score": score, "label": f"{name}:{score}"}


def exercise_celery():
    result = add.delay(11, 31)
    assert result.get(timeout=1) == 42
    payload = describe.delay("ada", 3).get(timeout=1)
    assert payload == {"name": "ada", "score": 3, "label": "ada:3"}


def exercise_kombu():
    with Connection("memory://") as connection:
        queue = connection.SimpleQueue("retrace-kombu")
        try:
            queue.put({"event": "created", "count": 3})
            message = queue.get(block=True, timeout=1)
            try:
                assert message.payload == {"event": "created", "count": 3}
            finally:
                message.ack()
        finally:
            queue.close()


def main():
    print("=== celery_kombu_memory_test ===")
    exercise_celery()
    exercise_kombu()
    print("celery and kombu memory transport record/replay scenario ok")


if __name__ == "__main__":
    main()
