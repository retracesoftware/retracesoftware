import os
import time
import uuid

from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError


BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:19092")


def wait_for_kafka():
    last_error = None
    for _ in range(90):
        admin = None
        try:
            admin = KafkaAdminClient(
                bootstrap_servers=BOOTSTRAP,
                client_id="retrace-wait",
                request_timeout_ms=3000,
            )
            admin.list_topics()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
        finally:
            if admin is not None:
                admin.close()
    raise RuntimeError(f"kafka did not become ready: {last_error}") from last_error


def main():
    print("=== kafka_live_broker_test ===")
    wait_for_kafka()

    topic = f"retrace-live-{uuid.uuid4().hex}"
    admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP, client_id="retrace-admin")
    try:
        try:
            admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])
        except TopicAlreadyExistsError:
            pass
    finally:
        admin.close()

    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        client_id="retrace-producer",
        key_serializer=lambda value: value.encode("utf-8"),
        value_serializer=lambda value: value.encode("utf-8"),
    )
    metadata = producer.send(topic, key="invoice", value="created").get(timeout=15)
    producer.flush()
    producer.close()
    assert metadata.topic == topic

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP,
        group_id=f"retrace-group-{uuid.uuid4().hex}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=10000,
        key_deserializer=lambda value: value.decode("utf-8"),
        value_deserializer=lambda value: value.decode("utf-8"),
    )
    try:
        messages = list(consumer)
    finally:
        consumer.close()

    assert [(message.key, message.value) for message in messages] == [("invoice", "created")]
    print("kafka live broker record/replay scenario ok")


if __name__ == "__main__":
    main()
