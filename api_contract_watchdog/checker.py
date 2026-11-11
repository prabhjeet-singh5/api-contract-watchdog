"""Make one request per endpoint and report independent checks."""

from datetime import datetime, timezone
from time import perf_counter

import httpx
from pydantic import ValidationError

from .baselines import BaselineStore
from .endpoints import Endpoint, dummyjson_endpoints
from .results import CheckOutcome, ContractError, EndpointResult
from .settings import Settings
from .tracking import track_result


def check_endpoint(
    client: httpx.Client, endpoint: Endpoint, settings: Settings
) -> EndpointResult:
    started = perf_counter()
    try:
        # Redirects must not hide an unexpected 3xx status code.
        response = client.get(
            endpoint.url,
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
    except httpx.RequestError as exc:
        elapsed_ms = (perf_counter() - started) * 1000
        message = "Request timed out" if isinstance(exc, httpx.TimeoutException) else "Request failed"
        checks = [
            CheckOutcome(
                check_type="network",
                status="failed",
                message=f"{message} ({type(exc).__name__}).",
            ),
            CheckOutcome(
                check_type="status_code",
                status="skipped",
                message="No complete HTTP response was received.",
            ),
            CheckOutcome(
                check_type="response_time",
                status="skipped",
                message="Request did not complete; elapsed time is recorded.",
            ),
            CheckOutcome(
                check_type="contract",
                status="skipped",
                message="No response body was available to validate.",
            ),
        ]
        return EndpointResult(
            endpoint=endpoint.name,
            url=endpoint.url,
            checked_at=datetime.now(timezone.utc),
            response_time_ms=round(elapsed_ms, 3),
            checks=checks,
        )

    # Client.get reads the full body. Measure before JSON parsing/validation.
    elapsed_ms = (perf_counter() - started) * 1000
    status_ok = response.status_code == endpoint.expected_status_code
    time_ok = elapsed_ms <= settings.max_response_time_ms
    checks = [
        CheckOutcome(
            check_type="network", status="passed", message="HTTP response received."
        ),
        CheckOutcome(
            check_type="status_code",
            status="passed" if status_ok else "failed",
            message=f"Expected {endpoint.expected_status_code}, received {response.status_code}.",
        ),
        CheckOutcome(
            check_type="response_time",
            status="passed" if time_ok else "failed",
            message=f"Response took {elapsed_ms:.3f} ms; limit is {settings.max_response_time_ms:g} ms.",
        ),
    ]

    payload = None
    payload_available = False
    try:
        payload = response.json()
        payload_available = True
    except ValueError:
        checks.append(CheckOutcome(
            check_type="contract", status="failed", message="Response is not valid JSON."
        ))
    else:
        try:
            endpoint.contract.model_validate(payload)
        except ValidationError as exc:
            # Field paths help diagnosis; input values may contain private data.
            errors = [
                ContractError(
                    path=list(error["loc"]),
                    error_type=error["type"],
                    message=error["msg"],
                )
                for error in exc.errors(
                    include_input=False, include_context=False, include_url=False
                )
            ]
            checks.append(CheckOutcome(
                check_type="contract",
                status="failed",
                message=f"{len(errors)} contract validation error(s).",
                errors=errors,
            ))
        else:
            checks.append(CheckOutcome(
                check_type="contract", status="passed", message="Response matches its contract."
            ))

    return EndpointResult(
        endpoint=endpoint.name,
        url=endpoint.url,
        checked_at=datetime.now(timezone.utc),
        status_code=response.status_code,
        response_time_ms=round(elapsed_ms, 3),
        checks=checks,
        payload=payload,
        payload_available=payload_available,
    )


def check_all(client: httpx.Client, settings: Settings) -> list[EndpointResult]:
    store = BaselineStore(settings.state_dir)
    return [
        track_result(check_endpoint(client, endpoint, settings), store)
        for endpoint in dummyjson_endpoints(settings)
    ]
