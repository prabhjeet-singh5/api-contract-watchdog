"""Contract for GET /users, including nested profile details."""

from .base import ContractModel, PaginatedResponse


class Hair(ContractModel):
    color: str
    type: str


class Coordinates(ContractModel):
    lat: float
    lng: float


class Address(ContractModel):
    address: str
    city: str
    state: str
    stateCode: str
    postalCode: str
    coordinates: Coordinates
    country: str


class Bank(ContractModel):
    cardExpire: str
    cardNumber: str
    cardType: str
    currency: str
    iban: str


class Company(ContractModel):
    department: str
    name: str
    title: str
    address: Address


class Crypto(ContractModel):
    coin: str
    wallet: str
    network: str


class User(ContractModel):
    id: int
    firstName: str
    lastName: str
    maidenName: str
    age: int
    gender: str
    email: str
    phone: str
    username: str
    password: str
    birthDate: str
    image: str
    bloodGroup: str
    height: float
    weight: float
    eyeColor: str
    hair: Hair
    ip: str
    address: Address
    macAddress: str
    university: str
    bank: Bank
    company: Company
    ein: str
    ssn: str
    userAgent: str
    crypto: Crypto
    role: str


class UsersResponse(PaginatedResponse):
    users: list[User]
