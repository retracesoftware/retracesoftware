class ProductNotFound(Exception):
    pass


class ProductService:
    def __init__(self, repository):
        self.repository = repository

    def visible_products(self, min_price=0):
        products = self.repository.list_products()
        visible = []

        for product in products:
            if product.price >= min_price:
                visible.append(product)

        return visible

    def product_detail(self, sku):
        product = self.repository.find_by_sku(sku)
        if product is None:
            raise ProductNotFound(sku)

        status = "in-stock" if product.available() else "sold-out"

        return {
            "sku": product.sku,
            "name": product.name,
            "price": product.price,
            "status": status,
        }
