"""Events acceptance fixture: Kafka and Redis producers."""
from kafka import KafkaProducer
import redis

def publish_order():
    KafkaProducer.send("orders.placed", b"order-data")

def send_alert():
    redis.publish("alerts:critical", "disk-full")

def dynamic_topic(name):
    # Dynamic first arg -- must be dropped per static-truth policy.
    KafkaProducer.send(name, b"x")
