from app.audit import build_audit_tags
from app.formatter import format_summary


class EmptyOrderError(Exception):
    pass


class OrderService:
    def __init__(self, repository):
        self.repository = repository

    def calculate_total(self, order):
        if not order.items:
            raise EmptyOrderError(order.order_id)

        subtotal = sum(price for price in order.items if price > 0)

        if order.vip:
            subtotal *= 0.9

        if subtotal >= 50:
            subtotal -= 5

        return subtotal

    def build_summaries(self):
        orders = self.repository.load_orders()
        audit_tags = build_audit_tags(orders)
        summaries = []

        for order in orders:
            try:
                total = self.calculate_total(order)
                status = "ok"
            except EmptyOrderError:
                total = 0
                status = "empty"

            summaries.append(
                format_summary(order, total, status, audit_tags[order.order_id])
            )

        return summaries
