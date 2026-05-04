from app.models import Product


class ProductRepository:
    def list_products(self):
        return [
            Product("A100", "Notebook", 12, 5),
            Product("B200", "Pencil", 2, 0),
            Product("C300", "Marker", 4, 8),
        ]

    def find_by_sku(self, sku):
        for product in self.list_products():
            if product.sku == sku:
                return product
        return None
