def format_summary(order_id, customer, total, status):
    return f"{order_id}: {customer} owes GBP {total:.2f} [{status}]"
