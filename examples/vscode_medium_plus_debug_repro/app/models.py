from dataclasses import dataclass


@dataclass
class Order:
    order_id: str
    customer: str
    items: list[int]
    vip: bool = False

    def item_count(self):
        return len(self.items)
