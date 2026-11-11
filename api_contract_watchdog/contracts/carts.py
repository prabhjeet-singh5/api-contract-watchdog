"""Contract for GET /carts and its nested line items."""

from .base import ContractModel, PaginatedResponse


class CartProduct(ContractModel):
    id: int
    title: str
    price: float
    quantity: int
    total: float
    discountPercentage: float
    discountedTotal: float
    thumbnail: str


class Cart(ContractModel):
    id: int
    products: list[CartProduct]
    total: float
    discountedTotal: float
    userId: int
    totalProducts: int
    totalQuantity: int


class CartsResponse(PaginatedResponse):
    carts: list[Cart]
