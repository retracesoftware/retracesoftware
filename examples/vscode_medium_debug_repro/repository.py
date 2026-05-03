class OrderRepository:
    def load_orders(self):
        return [
            {"id": "A100", "customer": "Ada", "items": [12, 8, 5], "vip": True},
            {"id": "B200", "customer": "Grace", "items": [30, 20], "vip": False},
            {"id": "C300", "customer": "Linus", "items": [], "vip": True},
        ]
