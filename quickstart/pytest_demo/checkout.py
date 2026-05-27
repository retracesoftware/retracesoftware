from __future__ import annotations

from dataclasses import dataclass
import json
import random
import time
import uuid
from pathlib import Path


@dataclass(frozen=True)
class CartItem:
    sku: str
    quantity: int
    unit_cents: int
    weight_oz: int


@dataclass(frozen=True)
class Customer:
    email: str
    loyalty_tier: str


@dataclass(frozen=True)
class Receipt:
    confirmation: str
    created_at: float
    audit_id: str
    subtotal_cents: int
    item_discount_cents: int
    loyalty_discount_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    audit: dict[str, object]


def load_promo_rules(path: Path) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_inventory(
    items: list[CartItem],
    inventory: dict[str, int],
) -> None:
    for item in items:
        if item.quantity <= 0:
            raise ValueError(f"{item.sku} quantity must be positive")
        if inventory.get(item.sku, 0) < item.quantity:
            raise ValueError(f"not enough inventory for {item.sku}")


def calculate_subtotal(items: list[CartItem]) -> int:
    return sum(item.quantity * item.unit_cents for item in items)


def calculate_weight(items: list[CartItem]) -> int:
    return sum(item.quantity * item.weight_oz for item in items)


def calculate_item_discount(
    items: list[CartItem],
    promo_rules: dict[str, int],
) -> int:
    discount = 0
    for item in items:
        percent = promo_rules.get(item.sku, 0)
        discount += round(item.quantity * item.unit_cents * percent / 100)
    return discount


def calculate_loyalty_discount(
    customer: Customer,
    discounted_subtotal_cents: int,
) -> int:
    if customer.loyalty_tier == "gold":
        return min(round(discounted_subtotal_cents * 0.10), 750)
    if customer.loyalty_tier == "silver":
        return min(round(discounted_subtotal_cents * 0.05), 400)
    return 0


def calculate_shipping(
    *,
    discounted_subtotal_cents: int,
    weight_oz: int,
    region: str,
) -> int:
    if discounted_subtotal_cents >= 6000 and region == "US":
        return 0
    return 399 + max(weight_oz - 16, 0) * 8


def build_receipt(
    items: list[CartItem],
    *,
    customer: Customer,
    inventory: dict[str, int],
    promo_rules: dict[str, int],
    tax_rate: float,
    region: str,
) -> Receipt:
    validate_inventory(items, inventory)

    subtotal_cents = calculate_subtotal(items)
    item_discount_cents = calculate_item_discount(items, promo_rules)
    discounted_subtotal_cents = subtotal_cents - item_discount_cents
    loyalty_discount_cents = calculate_loyalty_discount(
        customer,
        discounted_subtotal_cents,
    )
    weight_oz = calculate_weight(items)
    shipping_cents = calculate_shipping(
        discounted_subtotal_cents=discounted_subtotal_cents,
        weight_oz=weight_oz,
        region=region,
    )

    # Intentional quickstart bug: loyalty discount should reduce the taxable base.
    taxable_cents = discounted_subtotal_cents + shipping_cents
    tax_cents = round(taxable_cents * tax_rate)

    total_cents = (
        discounted_subtotal_cents
        - loyalty_discount_cents
        + shipping_cents
        + tax_cents
    )

    return Receipt(
        confirmation=f"{uuid.uuid4().hex[:8]}-{random.randint(1000, 9999)}",
        created_at=time.time(),
        audit_id=f"audit-{uuid.uuid4().hex[:12]}",
        subtotal_cents=subtotal_cents,
        item_discount_cents=item_discount_cents,
        loyalty_discount_cents=loyalty_discount_cents,
        shipping_cents=shipping_cents,
        tax_cents=tax_cents,
        total_cents=total_cents,
        audit={
            "customer": customer.email,
            "region": region,
            "weight_oz": weight_oz,
            "line_count": len(items),
            "generated_at": time.time(),
        },
    )
