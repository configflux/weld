"""gRPC acceptance fixture: client-side stub usage."""
import grpc
from orders.v1 import orders_pb2_grpc

def place_order(channel):
    stub = orders_pb2_grpc.OrderServiceStub(channel)
    stub.PlaceOrder(None)

def watch_orders(channel):
    stub = orders_pb2_grpc.OrderServiceStub(channel)
    stub.WatchOrders(None)
