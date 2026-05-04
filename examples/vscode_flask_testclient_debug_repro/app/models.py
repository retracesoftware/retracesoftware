from dataclasses import dataclass


@dataclass
class Product:
    sku: str
    name: str
    price: int
    stock: int

    def available(self):
        return self.stock > 0
