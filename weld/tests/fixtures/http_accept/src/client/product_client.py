"""HTTP acceptance fixture: product client (outbound HTTP calls)."""
import httpx

def fetch_products():
    return httpx.get("https://api.example.com/products")

def fetch_product(product_id):
    return httpx.get(f"https://api.example.com/products/{product_id}")

def create_product_remote():
    return httpx.post("https://api.example.com/products")
