"""DummyJSON response contracts, selected by endpoint path."""

from .base import ContractModel
from .carts import CartsResponse
from .products import Product, ProductsResponse
from .users import UsersResponse


DUMMYJSON_CONTRACTS: dict[str, type[ContractModel]] = {
    "/products": ProductsResponse,
    "/products/1": Product,
    "/carts": CartsResponse,
    "/users": UsersResponse,
}
