from __future__ import annotations

from dataclasses import dataclass
import random
import time
import uuid


@dataclass(frozen=True)
class CartItem:
    sku: str
    quantity: int
    unit_cents: int


@dataclass(frozen=True)
class Receipt:
    confirmation: str
    created_at: float
    subtotal_cents: int
    tax_cents: int
    discount_cents: int
    total_cents: int


def build_receipt(
    items: list[CartItem],
    *,
    tax_rate: float,
    discount_cents: int,
) -> Receipt:
    subtotal_cents = sum(item.quantity * item.unit_cents for item in items)
    tax_cents = round(subtotal_cents * tax_rate)

    # Intentional quickstart bug: tax is added twice.
    total_cents = subtotal_cents + tax_cents + tax_cents - discount_cents

    return Receipt(
        confirmation=f"{uuid.uuid4().hex[:8]}-{random.randint(1000, 9999)}",
        created_at=time.time(),
        subtotal_cents=subtotal_cents,
        tax_cents=tax_cents,
        discount_cents=discount_cents,
        total_cents=total_cents,
    )
