import os
import time

from celery import Celery
from kombu import Consumer, Exchange, Queue
from redis import Redis


BROKER_URL = os.getenv("REDIS_BROKER_URL", "redis://127.0.0.1:16380/9")


def wait_for_redis():
    client = Redis.from_url(BROKER_URL, socket_timeout=2)
    last_error = None
    for _ in range(60):
        try:
            client.ping()
            client.flushdb()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"redis broker did not become ready: {last_error}") from last_error


def main():
    print("=== celery_kombu_redis_broker_test ===")
    wait_for_redis()

    app = Celery("retrace_broker_demo", broker=BROKER_URL)
    exchange = Exchange("retrace.exchange", type="direct", durable=False)
    queue = Queue("retrace.queue", exchange=exchange, routing_key="invoice.created", durable=False)
    payload = {"invoice": "INV-001", "total": 42}
    received = []

    with app.connection_for_write() as connection:
        producer = app.amqp.Producer(connection)
        producer.publish(
            payload,
            exchange=exchange,
            routing_key="invoice.created",
            serializer="json",
            declare=[queue],
            retry=False,
        )

    with app.connection_for_read() as connection:
        bound_queue = queue(connection)
        bound_queue.declare()

        def handle(body, message):
            received.append(body)
            message.ack()

        with Consumer(connection, queues=[bound_queue], callbacks=[handle], accept=["json"]):
            connection.drain_events(timeout=5)

    assert received == [payload]
    print("celery/kombu redis broker record/replay scenario ok")


if __name__ == "__main__":
    main()
