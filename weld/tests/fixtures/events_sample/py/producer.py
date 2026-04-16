"""Fixture: static declarations of Kafka and Redis channels."""
from kafka import KafkaProducer
import redis

def send_order():
    # Static literal topic -- should be extracted.
    KafkaProducer.send("orders.events", b"payload")

def broadcast():
    # Static literal redis pub/sub channel -- should be extracted.
    redis.publish("notify:users", "hello")

def dynamic_topic(topic):
    # Dynamic first arg -- must be dropped per ADR 0018.
    KafkaProducer.send(topic, b"payload")

def fstring_topic(name):
    # f-string with substitution -- dynamic, must be dropped.
    KafkaProducer.send(f"orders.{name}", b"x")
