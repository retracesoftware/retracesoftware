from app.models import Order


class OrderRepository:
    def load_orders(self):
        raw_orders = [
            {"order_id": "A100", "customer": "Ada", "items": [12, 8, 5], "vip": True},
            {"order_id": "B200", "customer": "Grace", "items": [30, 20], "vip": False},
            {"order_id": "C300", "customer": "Linus", "items": [], "vip": True},
        ]

        return [Order(**raw) for raw in raw_orders]
