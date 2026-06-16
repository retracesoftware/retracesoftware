import asyncio

from aiokafka.structs import ConsumerRecord as AioConsumerRecord
from aiokafka.structs import TopicPartition as AioTopicPartition
from confluent_kafka import TopicPartition as ConfluentTopicPartition
from kafka import TopicPartition as KafkaTopicPartition
from kafka.partitioner.default import DefaultPartitioner


class StaticCluster:
    def __init__(self, topic, partitions):
        self._topic = topic
        self._partitions = set(partitions)

    def topics(self):
        return {self._topic}

    def partitions_for_topic(self, topic):
        assert topic == self._topic
        return self._partitions

    def available_partitions_for_topic(self, topic):
        assert topic == self._topic
        return self._partitions


def exercise_kafka_python():
    partitioner = DefaultPartitioner()
    if callable(partitioner):
        selected = partitioner(b"user-1", [0, 1, 2], [0, 1, 2])
    else:
        selected = partitioner.partition(
            "orders",
            key=b"user-1",
            serialized_key=b"user-1",
            value=None,
            serialized_value=None,
            cluster=StaticCluster("orders", [0, 1, 2]),
        )
    topic_partition = KafkaTopicPartition("orders", selected)
    assert topic_partition.topic == "orders"
    assert topic_partition.partition in {0, 1, 2}


async def exercise_aiokafka():
    topic_partition = AioTopicPartition("orders", 1)
    record = AioConsumerRecord(
        topic="orders",
        partition=topic_partition.partition,
        offset=7,
        timestamp=123456789,
        timestamp_type=0,
        key=b"user-1",
        value=b"created",
        checksum=None,
        serialized_key_size=6,
        serialized_value_size=7,
        headers=[],
    )
    await asyncio.sleep(0)
    assert record.topic == "orders"
    assert record.value == b"created"


def exercise_confluent_kafka():
    topic_partition = ConfluentTopicPartition("orders", 2, 11)
    assert topic_partition.topic == "orders"
    assert topic_partition.partition == 2
    assert topic_partition.offset == 11


def main():
    print("=== kafka_clients_test ===")
    exercise_kafka_python()
    asyncio.run(exercise_aiokafka())
    exercise_confluent_kafka()
    print("kafka client package record/replay scenario ok")


if __name__ == "__main__":
    main()
