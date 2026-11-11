"""Validate saved samples and deliberate contract breaks without a network."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from api_contract_watchdog.contracts import DUMMYJSON_CONTRACTS, Product


FIXTURES = Path(__file__).parent / "fixtures"
SAMPLES = [
    ("/products", "products_good.json", ("products", 0, "price")),
    ("/products/1", "product_good.json", ("price",)),
    ("/carts", "carts_good.json", ("carts", 0, "products", 0, "quantity")),
    ("/users", "users_good.json", ("users", 0, "age")),
]


def load_sample(filename):
    return json.loads((FIXTURES / filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize("endpoint,filename,path", SAMPLES)
def test_saved_good_response_passes(endpoint, filename, path):
    result = DUMMYJSON_CONTRACTS[endpoint].model_validate(load_sample(filename))
    assert result.model_dump() == load_sample(filename)


@pytest.mark.parametrize("endpoint,filename,path", SAMPLES)
@pytest.mark.parametrize("break_type", ["missing", "wrong_type", "null"])
def test_required_field_break_is_rejected(endpoint, filename, path, break_type):
    payload = load_sample(filename)
    parent = payload
    for key in path[:-1]:
        parent = parent[key]
    field = path[-1]
    if break_type == "missing":
        del parent[field]
    elif break_type == "wrong_type":
        # Numeric strings must fail instead of being silently converted.
        parent[field] = str(parent[field])
    else:
        parent[field] = None

    with pytest.raises(ValidationError) as exc:
        DUMMYJSON_CONTRACTS[endpoint].model_validate(payload)
    assert path in [error["loc"] for error in exc.value.errors()]


@pytest.mark.parametrize("field,value", [("id", True), ("stock", 1.5), ("price", True)])
def test_boolean_and_fractional_integer_changes_are_rejected(field, value):
    payload = load_sample("product_good.json")
    payload[field] = value
    with pytest.raises(ValidationError):
        Product.model_validate(payload)


def test_nested_user_field_break_is_rejected():
    payload = load_sample("users_good.json")
    del payload["users"][0]["company"]["address"]["coordinates"]["lat"]
    with pytest.raises(ValidationError) as exc:
        DUMMYJSON_CONTRACTS["/users"].model_validate(payload)
    assert exc.value.errors()[0]["loc"] == (
        "users", 0, "company", "address", "coordinates", "lat"
    )


@pytest.mark.parametrize("endpoint,filename", [(row[0], row[1]) for row in SAMPLES if row[0] != "/products/1"])
@pytest.mark.parametrize("field", ["total", "skip", "limit"])
def test_missing_pagination_is_rejected(endpoint, filename, field):
    payload = load_sample(filename)
    del payload[field]
    with pytest.raises(ValidationError) as exc:
        DUMMYJSON_CONTRACTS[endpoint].model_validate(payload)
    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.parametrize("field", ["total", "skip", "limit"])
def test_negative_pagination_is_rejected(field):
    payload = load_sample("products_good.json")
    payload[field] = -1
    with pytest.raises(ValidationError):
        DUMMYJSON_CONTRACTS["/products"].model_validate(payload)


def test_additional_fields_are_preserved_at_every_level():
    payload = load_sample("products_good.json")
    payload["newEnvelopeField"] = "new"
    payload["products"][0]["newProductField"] = 123
    payload["products"][0]["dimensions"]["newDimensionField"] = True
    result = DUMMYJSON_CONTRACTS["/products"].model_validate(payload)
    assert result.model_dump() == payload


@pytest.mark.parametrize("brand", ["missing", None])
def test_brand_is_optional_and_nullable(brand):
    payload = load_sample("product_good.json")
    if brand == "missing":
        del payload["brand"]
    else:
        payload["brand"] = brand
    assert Product.model_validate(payload).brand is None


def test_brand_still_rejects_a_number():
    payload = load_sample("product_good.json")
    payload["brand"] = 123
    with pytest.raises(ValidationError):
        Product.model_validate(payload)


def test_whole_number_price_is_a_valid_json_number():
    payload = load_sample("product_good.json")
    payload["price"] = 10
    assert Product.model_validate(payload).price == 10.0


@pytest.mark.parametrize("endpoint,filename,collection", [
    ("/products", "products_good.json", "products"),
    ("/carts", "carts_good.json", "carts"),
    ("/users", "users_good.json", "users"),
])
def test_empty_page_is_valid(endpoint, filename, collection):
    payload = load_sample(filename)
    payload[collection] = []
    payload["total"] = 0
    assert getattr(DUMMYJSON_CONTRACTS[endpoint].model_validate(payload), collection) == []
