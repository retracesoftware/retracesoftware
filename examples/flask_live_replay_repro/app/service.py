class InventoryService:
    def __init__(self):
        self.products = {
            "A100": {"name": "Notebook", "price": 12, "stock": 5},
            "B200": {"name": "Pencil", "price": 2, "stock": 0},
            "C300": {"name": "Marker", "price": 4, "stock": 8},
        }

    def list_products(self, min_price=0):
        result = []
        for sku, product in sorted(self.products.items()):
            if product["price"] >= min_price:
                result.append(
                    {
                        "sku": sku,
                        "name": product["name"],
                        "price": product["price"],
                        "available": product["stock"] > 0,
                    }
                )
        return result

    def quote(self, sku, quantity):
        product = self.products.get(sku)
        if product is None:
            return {"error": "not-found", "sku": sku}

        return {
            "sku": sku,
            "quantity": quantity,
            "total": product["price"] * quantity,
            "available": product["stock"] >= quantity,
        }
