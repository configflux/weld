"""HTTP acceptance fixture: order client (outbound HTTP calls)."""
import requests

def place_order():
    return requests.post("https://api.example.com/orders")

def get_orders():
    return requests.get("https://api.example.com/orders")
