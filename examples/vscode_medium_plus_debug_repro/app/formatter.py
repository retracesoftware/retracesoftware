def format_summary(order, total, status, audit_tag):
    return (
        f"{order.order_id}: {order.customer} "
        f"items={order.item_count()} total=GBP {total:.2f} "
        f"status={status} audit={audit_tag}"
    )
