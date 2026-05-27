from __future__ import annotations

import json

import pytest

from pytest_demo.checkout import (
    CartItem,
    Customer,
    build_receipt,
    calculate_item_discount,
    calculate_loyalty_discount,
    calculate_shipping,
    calculate_subtotal,
    calculate_weight,
    load_promo_rules,
    validate_inventory,
)


def sample_cart() -> list[CartItem]:
    return [
        CartItem(sku="notebook", quantity=2, unit_cents=1200, weight_oz=10),
        CartItem(sku="pencil", quantity=3, unit_cents=250, weight_oz=1),
        CartItem(sku="backpack", quantity=1, unit_cents=4200, weight_oz=22),
    ]


def sample_inventory() -> dict[str, int]:
    return {
        "notebook": 12,
        "pencil": 50,
        "backpack": 2,
    }


def sample_customer() -> Customer:
    return Customer(email="ada@example.com", loyalty_tier="gold")


def sample_promo_rules() -> dict[str, int]:
    return {
        "pencil": 20,
    }


def make_receipt():
    return build_receipt(
        sample_cart(),
        customer=sample_customer(),
        inventory=sample_inventory(),
        promo_rules=sample_promo_rules(),
        tax_rate=0.0825,
        region="US",
    )


def test_promo_rules_load_from_json(tmp_path) -> None:
    promo_file = tmp_path / "promos.json"
    promo_file.write_text(json.dumps({"pencil": 20, "backpack": 5}), encoding="utf-8")

    assert load_promo_rules(promo_file) == {"pencil": 20, "backpack": 5}


def test_inventory_accepts_available_items() -> None:
    validate_inventory(sample_cart(), sample_inventory())


def test_inventory_rejects_unknown_sku() -> None:
    with pytest.raises(ValueError, match="not enough inventory"):
        validate_inventory(
            [CartItem(sku="eraser", quantity=1, unit_cents=100, weight_oz=1)],
            sample_inventory(),
        )


def test_inventory_rejects_zero_quantity() -> None:
    with pytest.raises(ValueError, match="quantity must be positive"):
        validate_inventory(
            [CartItem(sku="pencil", quantity=0, unit_cents=250, weight_oz=1)],
            sample_inventory(),
        )


def test_subtotal_is_quantity_times_unit_price() -> None:
    assert calculate_subtotal(sample_cart()) == 7350


def test_weight_counts_each_unit() -> None:
    assert calculate_weight(sample_cart()) == 45


def test_item_discount_uses_sku_promo_percent() -> None:
    assert calculate_item_discount(sample_cart(), sample_promo_rules()) == 150


def test_gold_loyalty_discount_is_capped() -> None:
    customer = sample_customer()

    assert calculate_loyalty_discount(customer, discounted_subtotal_cents=7200) == 720
    assert calculate_loyalty_discount(customer, discounted_subtotal_cents=12000) == 750


def test_us_shipping_is_free_over_threshold() -> None:
    assert calculate_shipping(
        discounted_subtotal_cents=7200,
        weight_oz=45,
        region="US",
    ) == 0


def test_international_shipping_charges_by_weight() -> None:
    assert calculate_shipping(
        discounted_subtotal_cents=7200,
        weight_oz=45,
        region="EU",
    ) == 631


def test_receipt_has_runtime_context() -> None:
    receipt = make_receipt()

    assert receipt.confirmation
    assert receipt.audit_id.startswith("audit-")
    assert receipt.created_at > 0
    assert receipt.audit["generated_at"] > 0


def test_receipt_breakdown_is_visible_for_debugging() -> None:
    receipt = make_receipt()

    assert receipt.subtotal_cents == 7350
    assert receipt.item_discount_cents == 150
    assert receipt.loyalty_discount_cents == 720
    assert receipt.shipping_cents == 0
    assert receipt.audit["line_count"] == 3
    assert receipt.audit["weight_oz"] == 45


def test_total_taxes_discounted_amount_once() -> None:
    receipt = make_receipt()

    assert receipt.total_cents == 7015
