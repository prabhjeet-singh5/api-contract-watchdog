"""Contracts for GET /products and GET /products/1."""

from .base import ContractModel, PaginatedResponse


class Dimensions(ContractModel):
    width: float
    height: float
    depth: float


class Review(ContractModel):
    rating: int
    comment: str
    date: str
    reviewerName: str
    reviewerEmail: str


class ProductMetadata(ContractModel):
    createdAt: str
    updatedAt: str
    barcode: str
    qrCode: str


class Product(ContractModel):
    id: int
    title: str
    description: str
    category: str
    price: float
    discountPercentage: float
    rating: float
    stock: int
    tags: list[str]
    # Some products (for example, groceries) have no brand.
    brand: str | None = None
    sku: str
    weight: float
    dimensions: Dimensions
    warrantyInformation: str
    shippingInformation: str
    availabilityStatus: str
    reviews: list[Review]
    returnPolicy: str
    minimumOrderQuantity: int
    meta: ProductMetadata
    thumbnail: str
    images: list[str]


class ProductsResponse(PaginatedResponse):
    products: list[Product]
