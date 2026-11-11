# Contract samples

These small, synthetic JSON responses follow the field structure documented by
DummyJSON. They are hand-written test data, not captured production responses.
All identities, credentials, and financial details are fake. The `.invalid`
domains deliberately cannot point to a real service.

- `products_good.json`: a paginated list with one product.
- `product_good.json`: the same product without the pagination wrapper.
- `carts_good.json`: a paginated list with one cart and one line item.
- `users_good.json`: a paginated list with one user and nested profile objects.

Phase 2 tests make deliberately broken copies in memory. Saved broken samples
and broader checker/diff regression tests will be added in Phase 5.

Sources for the field structure:

- [Products](https://dummyjson.com/docs/products)
- [Carts](https://dummyjson.com/docs/carts)
- [Users](https://dummyjson.com/docs/users)
