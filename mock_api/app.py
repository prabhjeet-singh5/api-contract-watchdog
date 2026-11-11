"""A DummyJSON-shaped demo API whose responses can be broken on purpose."""

import asyncio
import copy
import json
import os
import secrets
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

DATA_DIR = Path(__file__).parent / "data"
MIN_TOKEN_LENGTH = 16

# Name: (path, list key or None for a single object, field targeted by breaks).
ENDPOINTS = {
    "products": ("/products", "products", "price"),
    "single_product": ("/products/1", None, "price"),
    "carts": ("/carts", "carts", "total"),
    "users": ("/users", "users", "age"),
}

EndpointName = Literal["products", "single_product", "carts", "users"]
BreakMode = Literal[
    "remove_field", "type_change", "new_null", "added_field",
    "server_error", "invalid_json", "slow",
]


class Break(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: BreakMode
    # Used only by "slow"; capped so a public demo cannot hold connections open long.
    delay_ms: int = Field(default=3000, ge=1, le=10_000)


def broken_payload(payload: Any, items_key: str | None, field: str, mode: BreakMode) -> Any:
    payload = copy.deepcopy(payload)
    for item in payload[items_key] if items_key else [payload]:
        if mode == "remove_field":
            item.pop(field, None)
        elif mode == "type_change":
            item[field] = str(item[field])
        elif mode == "new_null":
            item[field] = None
        elif mode == "added_field":
            item["mockExtra"] = "Added by the mock API."
    return payload


def create_app(admin_token: str | None = None) -> FastAPI:
    """Build the app; the token defaults to MOCK_API_ADMIN_TOKEN.

    Without a token the data routes still work, but admin controls are disabled.
    """
    token = admin_token or os.environ.get("MOCK_API_ADMIN_TOKEN") or None
    if token is not None and len(token) < MIN_TOKEN_LENGTH:
        raise ValueError(f"MOCK_API_ADMIN_TOKEN must be at least {MIN_TOKEN_LENGTH} characters")

    payloads = {
        name: json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        for name in ENDPOINTS
    }
    # In-memory and per process: run one worker, and expect a restart to clear breaks.
    breaks: dict[str, Break] = {}
    app = FastAPI(title="api-contract-watchdog mock API")
    bearer = HTTPBearer(auto_error=False)

    def require_admin(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if token is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Admin controls are disabled; set MOCK_API_ADMIN_TOKEN.",
            )
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode("utf-8"), token.encode("utf-8")
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid admin token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def add_data_route(name: str, path: str, items_key: str | None, field: str) -> None:
        async def serve() -> Response:
            active = breaks.get(name)
            if active is None or active.mode == "slow":
                if active is not None:
                    await asyncio.sleep(active.delay_ms / 1000)
                return JSONResponse(payloads[name])
            if active.mode == "server_error":
                return JSONResponse({"message": "Simulated server error."}, status_code=500)
            if active.mode == "invalid_json":
                return Response("{simulated invalid JSON", media_type="application/json")
            return JSONResponse(broken_payload(payloads[name], items_key, field, active.mode))

        app.add_api_route(path, serve, methods=["GET"], name=name)

    for name, (path, items_key, field) in ENDPOINTS.items():
        add_data_route(name, path, items_key, field)

    @app.get("/")
    def index() -> dict[str, Any]:
        # A visitor opening the bare URL should see what exists, not a 404.
        return {
            "service": "api-contract-watchdog mock API",
            "endpoints": [path for path, _, _ in ENDPOINTS.values()],
            "health": "/health",
            "docs": "/docs",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    admin = [Depends(require_admin)]

    @app.get("/admin/breaks", dependencies=admin)
    def list_breaks() -> dict[str, Break]:
        return breaks

    @app.put("/admin/breaks/{endpoint}", dependencies=admin)
    def set_break(endpoint: EndpointName, change: Break) -> dict[str, Break]:
        breaks[endpoint] = change
        return breaks

    @app.delete("/admin/breaks/{endpoint}", dependencies=admin)
    def clear_break(endpoint: EndpointName) -> dict[str, Break]:
        breaks.pop(endpoint, None)
        return breaks

    @app.delete("/admin/breaks", dependencies=admin)
    def clear_all_breaks() -> dict[str, Break]:
        breaks.clear()
        return breaks

    return app
