from __future__ import annotations

from pytest_demo.checkout import CartItem, build_receipt


def sample_cart() -> list[CartItem]:
    return [
        CartItem(sku="notebook", quantity=2, unit_cents=1200),
        CartItem(sku="pencil", quantity=3, unit_cents=250),
    ]


def test_receipt_has_runtime_context() -> None:
    receipt = build_receipt(sample_cart(), tax_rate=0.08, discount_cents=300)

    assert receipt.confirmation
    assert receipt.created_at > 0


def test_subtotal_is_quantity_times_unit_price() -> None:
    receipt = build_receipt(sample_cart(), tax_rate=0.08, discount_cents=300)

    assert receipt.subtotal_cents == 3150


def test_total_applies_tax_and_discount_once() -> None:
    receipt = build_receipt(sample_cart(), tax_rate=0.08, discount_cents=300)

    assert receipt.total_cents == 3102
