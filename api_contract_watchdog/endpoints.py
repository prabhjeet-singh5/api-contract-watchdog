"""Endpoint definitions, separate from the HTTP checking logic."""

from dataclasses import dataclass

from .contracts import DUMMYJSON_CONTRACTS
from .contracts.base import ContractModel
from .settings import Settings


@dataclass(frozen=True)
class Endpoint:
    name: str
    url: str
    contract: type[ContractModel]
    expected_status_code: int = 200


def dummyjson_endpoints(settings: Settings) -> list[Endpoint]:
    base_url = str(settings.dummyjson_base_url).rstrip("/")
    names = {
        "/products": "products",
        "/products/1": "single_product",
        "/carts": "carts",
        "/users": "users",
    }
    return [
        Endpoint(name=names[path], url=base_url + path, contract=contract)
        for path, contract in DUMMYJSON_CONTRACTS.items()
    ]
