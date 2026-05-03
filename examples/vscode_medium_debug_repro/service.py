from formatter import format_summary


class EmptyOrderError(Exception):
    pass


class OrderService:
    def __init__(self, repository):
        self.repository = repository

    def calculate_total(self, order):
        if not order["items"]:
            raise EmptyOrderError(order["id"])

        subtotal = 0
        for price in order["items"]:
            subtotal += price

        if order["vip"]:
            subtotal *= 0.9

        if subtotal >= 50:
            subtotal -= 5

        return subtotal

    def build_summaries(self):
        summaries = []

        for order in self.repository.load_orders():
            try:
                total = self.calculate_total(order)
                status = "ok"
            except EmptyOrderError:
                total = 0
                status = "empty"

            summaries.append(
                format_summary(order["id"], order["customer"], total, status)
            )

        return summaries
