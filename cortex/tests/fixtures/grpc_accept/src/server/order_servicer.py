"""gRPC acceptance fixture: server-side servicer implementation."""
from orders.v1 import orders_pb2_grpc

class OrderServiceServicer(orders_pb2_grpc.OrderServiceServicer):
    """Implements the OrderService gRPC service."""

    def PlaceOrder(self, request, context):
        pass

    def GetOrder(self, request, context):
        pass

    def WatchOrders(self, request, context):
        pass
