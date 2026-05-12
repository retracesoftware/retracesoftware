from attrs import define
from cattrs import Converter


@define
class LineItem:
    sku: str
    quantity: int


@define
class Order:
    id: int
    items: list[LineItem]


def main():
    print("=== attrs_cattrs_test ===")
    converter = Converter()
    order = Order(id=101, items=[LineItem("book", 2), LineItem("pen", 5)])
    raw = converter.unstructure(order)
    rebuilt = converter.structure(raw, Order)

    assert rebuilt == order
    total = sum(item.quantity for item in rebuilt.items)
    print(f"order={rebuilt.id} total={total}")
    print("attrs/cattrs ok")


if __name__ == "__main__":
    main()
