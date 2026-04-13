"""Events acceptance fixture: Kafka consumer."""
from kafka import KafkaConsumer

def consume_orders():
    KafkaConsumer.subscribe(["orders.placed"])
