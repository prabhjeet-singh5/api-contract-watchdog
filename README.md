# api-contract-watchdog

A Python project being built in ten phases. The planned tool will check API
responses against contracts, describe changes, and alert on failures and recovery.

## Current state: Phase 5 - run history and state changes

The checker can call four DummyJSON endpoints, validate response contracts,
check status codes, measure response times, compare response shapes with
saved baselines, record each run, and detect when an endpoint starts failing or
recovers. Its tests run offline. Alerts, scheduling, and publishing are planned
for later phases.

Requires Python 3.11 or newer.

## Local setup

PowerShell (Windows):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m pytest -q
```

macOS / Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m pytest -q
```

Run the copy command only when you do not already have a `.env` file. Calling
the virtual environment's Python directly avoids needing to activate it.
All tests run locally against saved samples without calling an API.

## Response contracts

| Endpoint path | Pydantic model | What it validates |
| --- | --- | --- |
| `/products` | `ProductsResponse` | Products and pagination metadata. |
| `/products/1` | `Product` | A single product, including dimensions, reviews, and metadata. |
| `/carts` | `CartsResponse` | Carts, their line items, and pagination metadata. |
| `/users` | `UsersResponse` | Users, nested profile details, and pagination metadata. |

The models follow DummyJSON's documented field structure:
[products](https://dummyjson.com/docs/products),
[carts](https://dummyjson.com/docs/carts), and
[users](https://dummyjson.com/docs/users).
`DUMMYJSON_CONTRACTS` maps each endpoint path to its model for the checker.

Contract rules:

- Required fields must be present and cannot be null.
- Types are strict: a number returned as a string fails validation. Booleans
  cannot stand in for numbers; IDs and counts must be integers.
- Prices and measurements accept both whole and decimal JSON numbers.
- `brand` is the only optional, nullable modeled field. Some products omit it
  ([example](https://dummyjson.com/products/16)). New nulls will also be compared
  against the saved response shape by the diff check.
- Extra fields are allowed and preserved, including in nested objects. Additions
  are reported by the diff without failing the endpoint.
- Pagination metadata is required and nonnegative; empty lists are valid.

The contracts check field presence and types, not exact values or date/email/URL
formats. Field names match the API's JSON names so errors point directly to the
response field. Samples in `tests/fixtures/` are synthetic, with fake identities
and credentials; they are not claims about live API results.

## Run a check

From the repository root, run once against the configured API:

```powershell
.\.venv\Scripts\python.exe -m api_contract_watchdog
```

On macOS / Linux, use `.venv/bin/python -m api_contract_watchdog`.
This command makes real HTTP requests. Running pytest makes no live API calls.

The checker requests `/products`, `/products/1`, `/carts`, and `/users` in order.
Each endpoint returns a result with its name, URL, UTC check timestamp, HTTP
status code, elapsed milliseconds, overall `passed` flag, a `diff` report, and
six check outcomes:

- `network`: whether a complete HTTP response arrived.
- `status_code`: whether the status is the expected `200`. Redirects are not followed.
- `response_time`: whether the completed request stayed within the configured limit.
- `contract`: whether the body is valid JSON and matches the Pydantic model.
- `diff`: whether the response shape has breaking changes from its baseline.
- `baseline`: whether a passing response's shape could be saved.

Outcomes are `passed`, `failed`, or `skipped`, with plain-language messages.
An endpoint passes when no check fails. On the first good response, the diff is
skipped because there is no previous baseline; saving the initial baseline can
still pass. Checks are independent: an error
response can fail its status code and contract together. A timeout or connection
error fails the network check and skips checks that need a completed response;
the remaining endpoints are still checked.

The timer covers the request and full body download, excluding JSON parsing and
contract validation. The threshold comparison uses the unrounded duration;
reported milliseconds are rounded to three decimal places. A duration equal to
the limit passes. Validation errors include field paths, error types, and messages,
without copying input values. The raw JSON is retained only in the in-memory result
for the diff; it is excluded from printed JSON and baseline files.

The command prints one JSON run record: a `run_id`, `started_at` and
`finished_at` timestamps, an overall `passed` flag, the endpoint `results`, and
the state `changes` described below. Exit codes are `0` when all endpoints pass
and the run was recorded, `1` when any endpoint fails or the run history could
not be recorded, and `2` for invalid configuration. Retries and alerts are not
implemented yet.

## Response shapes and diffs

The first response that passes its network, status, timing, and contract checks
creates a baseline. Later JSON responses are compared with that baseline, even
when another check fails. A timeout or invalid JSON body skips the comparison.
The `diff` field is `null` when no comparison was possible.

| Change | Diff field | Effect |
| --- | --- | --- |
| An observed field disappears | `removed_fields` | Endpoint fails. |
| A field is first observed | `added_fields` | Informational; a passing baseline is updated. |
| A field gains a previously unseen non-null type | `type_changes` | Endpoint fails. |
| A previously non-null field returns null | `new_nulls` | Endpoint fails. |

Each type change lists the path, previous types, and current types. Field paths
use `$` for the root, quoted keys for object fields, and `[*]` for array items.
For example, `$["products"][*]["price"]` means the price field in any product.
Quoted keys also keep a literal `"a.b"` field distinct from a nested `a`/`b` field.
Removing an entire object reports its parent path once rather than every child.
A new null on an object reports that path without treating its children as removed.

Shape rules:

- Shapes contain paths and JSON types, not response values. Strings, numbers,
  booleans, objects, arrays, and nulls are distinguished. Whole and decimal numbers
  share the `number` type; stricter integer rules still belong to the contracts.
- Value changes, item order, and list length do not change the shape.
- All array items contribute their fields and types. A field is reported removed
  only when it disappears from all observed items; contracts still detect a missing
  required field on an individual item.
- An empty array provides no item-shape evidence. Its previously known item paths
  remain in the baseline. An initial empty array learns its item shape when items
  first appear.
- Previously observed type variants are retained across passing samples, so a
  shorter sample of a mixed array does not erase accepted types. A null-only field
  can reveal its first non-null type without failing. A new field whose initial
  value is null is reported as an addition, not a new-null regression.

Baselines live in `STATE_DIR/baselines/`, with one JSON file named by the SHA-256
hash of the full endpoint URL. Different base URLs get separate baselines. Each
file records a format version, endpoint name, URL, last good UTC timestamp, and
shape. Passing results update it using an atomic file replacement. Failed results
leave it intact, so repeated breaks are compared with the same good expectations.

An unreadable or corrupt baseline fails the `baseline` check; it is not silently
replaced. A write failure also fails that check. The remaining endpoints continue.
If you deliberately accept a changed contract, first update its Pydantic model if
needed, then move that endpoint's baseline file aside to a backup location before
running again. The next fully passing response establishes new expectations.
Keep runs sequential; coordinating simultaneous writers is not implemented.

## Run history and state changes

Every run is appended as one compact JSON line to
`STATE_DIR/history/runs.jsonl`, using the same fields that are printed. Response
bodies are never stored. The file grows by one line per run; rotation is not
implemented yet.

`STATE_DIR/state.json` holds each endpoint's current status, keyed by URL:
`passing` or `failing`, `since` (the first check in that status),
`last_checked_at`, and `consecutive_failures`. Each run compares its results with
that file and reports transitions in `changes`:

| Previous status | Current result | Change |
| --- | --- | --- |
| None (first run) | Passes | None. |
| None (first run) | Fails | `failed` |
| `passing` | Fails | `failed` |
| `failing` | Fails | None; `consecutive_failures` increases. |
| `failing` | Passes | `recovered` |

Each change lists the endpoint, URL, change type, when it happened (`at`), and
`failing_since`. For `recovered`, `failing_since` is when the outage began. A
`failed` change also lists which check types failed. Repeated failures produce
one `failed` change, not one per run, so later alerts are not repeated.

The run is appended to history before state is saved. If either write fails,
the next run reports the same transitions again rather than missing them. An
unreadable or corrupt `state.json` is not overwritten: the run still prints its
results, `changes` is `null`, an error goes to stderr, and the exit code is `1`.
To reset the tracking, move `state.json` aside; the next run starts fresh.

### Checker configuration

The command loads `.env` from the current working directory. Existing environment
variables take precedence. These settings are active:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `DUMMYJSON_BASE_URL` | `https://dummyjson.com` | HTTP/HTTPS base URL, optionally including a path; no query, fragment, or embedded credentials. |
| `REQUEST_TIMEOUT_SECONDS` | `10` | Network inactivity timeout; positive, finite seconds. |
| `MAX_RESPONSE_TIME_MS` | `2000` | Completed-response limit; positive, finite milliseconds. |
| `STATE_DIR` | `data` | Directory for baselines, run state, and run history; a relative path is resolved from the working directory. |

HTTPX applies the timeout separately to connection, reading, writing, and waiting
for a connection pool slot. It is not a total request-duration deadline; the
response-time check measures the completed request separately.
[HTTPX timeout documentation](https://www.python-httpx.org/advanced/timeouts/).

## Files and folders

| Path | Purpose |
| --- | --- |
| `api_contract_watchdog/__main__.py` | Command entry point, run record output, and exit codes. |
| `api_contract_watchdog/settings.py` | Validated environment configuration and local `.env` loading. |
| `api_contract_watchdog/endpoints.py` | Endpoint URLs, names, expected statuses, and contract selection. |
| `api_contract_watchdog/results.py` | Result, check outcome, and validation-error models. |
| `api_contract_watchdog/checker.py` | HTTP requests, timing, status checks, and contract validation. |
| `api_contract_watchdog/diff.py` | Extracts value-free shapes and compares paths and types. |
| `api_contract_watchdog/baselines.py` | Loads versioned baselines and atomically saves passing shapes. |
| `api_contract_watchdog/tracking.py` | Attaches diffs and preserves the last good baseline on failures. |
| `api_contract_watchdog/history.py` | Records runs, tracks endpoint state, and detects failures and recoveries. |
| `api_contract_watchdog/storage.py` | Atomic file replacement shared by the baseline and state stores. |
| `api_contract_watchdog/contracts/` | Shared validation rules, products, carts, users, and endpoint-to-model mapping. |
| `tests/` | Offline setup, contract, settings, checker, CLI, diff, baseline, tracking, and history tests; `fixtures/` holds synthetic JSON samples. |
| `mock_api/` | FastAPI demo server, starting in Phase 6. |
| `data/` | Local response baselines and run history; contents are ignored by Git. |
| `n8n/` | Workflow and example payload, starting in Phase 8. |
| `scripts/` | Small helper scripts for running demos. |
| `status/` | Status page files, starting in Phase 9. |
| `docs/` | Demo screenshots and supporting documentation. |
| `.github/workflows/` | GitHub Actions workflows, starting in Phase 9. |
| `requirements.txt` | Exact versions for the project's direct dependencies. |
| `pyproject.toml` | Pytest configuration. |
| `.env.example` | Configuration template with empty secret values. |
| `.gitignore` | Excludes credentials, the virtual environment, and generated files. |

Empty folders use `.gitkeep` placeholders so Git can retain them.

## Configuration and secrets

`.env.example` includes active checker settings and placeholders for later phases.
The checker currently reads only the four variables listed above. The default
`data/` directory is ignored by Git; if you choose another state directory inside
the repository, add that directory to `.gitignore` too.
Keep real credentials in `.env` locally and in GitHub Actions secrets for CI.
No accounts, deployed services, or GitHub Secrets are needed for Phase 5.

Later phases will provide exact setup steps for these GitHub Secrets:

- `MOCK_API_BASE_URL`: the deployed mock API's base URL.
- `MOCK_API_ADMIN_TOKEN`: the mock server's admin token, used for demo controls.
- `N8N_WEBHOOK_URL`: the n8n production webhook URL.
- `SLACK_WEBHOOK_URL`: the fallback Slack incoming webhook URL.

The mock API server will also need `MOCK_API_ADMIN_TOKEN` in its hosting
environment. Do not put tokens or webhook URLs in source code or screenshots.
