# api-contract-watchdog

A Python project being built in ten phases. The planned tool will check API
responses against contracts, describe changes, and alert on failures and recovery.

## Current state: Phase 8 - n8n alert workflow

The checker can call four DummyJSON endpoints, validate response contracts,
check status codes, measure response times, compare response shapes with
saved baselines, record each run, and detect when an endpoint starts failing or
recovers. Each failure or recovery sends one alert to an n8n webhook, with a
Slack webhook as a fallback. An importable n8n workflow turns each alert into a
Slack message. A mock API serves the same endpoints and can break them on
request, so every failure type can be demonstrated. Its tests run offline.
Scheduling and publishing are planned for later phases.

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
the state `changes` and `alert` outcome described below. Exit codes are `0` when
all endpoints pass and the run was recorded, `1` when any endpoint fails, the run
history could not be recorded, or an alert could not be delivered, and `2` for
invalid configuration. Request retries are not implemented.

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

## Alerts

When a run has at least one `failed` or `recovered` change, the watchdog sends one
alert covering all of them. Runs without changes send nothing, so an endpoint that
stays broken alerts once when it fails and once when it recovers.

1. If `N8N_WEBHOOK_URL` is set, the full JSON payload is POSTed there.
2. If n8n is not set, returns a non-2xx status, or cannot be reached, a
   `{"text": ...}` message is POSTed to `SLACK_WEBHOOK_URL`, if set.
3. If neither is set, the alert is `skipped`; this is normal for local runs.

The run record's `alert` field shows `sent` (with the `channel`), `skipped`, or
`failed`; it is `null` when there were no changes. When delivery fails, the run
exits `1` and state is not saved, so the next run detects the same changes and
tries the alert again. History still records the failed attempt. Each webhook
gets one attempt per run, with `REQUEST_TIMEOUT_SECONDS` as its timeout, and
redirects are not followed.

The n8n payload:

```json
{
  "version": 1,
  "source": "api-contract-watchdog",
  "run_id": "4f0c...",
  "run_url": "https://github.com/.../actions/runs/123",
  "finished_at": "2026-11-11T16:20:00Z",
  "summary": "1 endpoint(s) failed, 0 recovered.",
  "text": "API contract watchdog: 1 endpoint(s) failed, 0 recovered.\nFAILED carts (...): status_code, contract, diff.\n  - status_code: Expected 200, received 500.\n...",
  "changes": [
    {
      "endpoint": "carts",
      "url": "https://api-contract-watchdog-mock.onrender.com/carts",
      "change": "failed",
      "at": "2026-11-11T16:20:00Z",
      "failing_since": "2026-11-11T16:20:00Z",
      "failed_checks": ["status_code", "contract", "diff"],
      "details": ["status_code: Expected 200, received 500.", "..."]
    }
  ]
}
```

`text` is a ready-to-post plain message, the same one Slack receives. A recovery
reads, for example, `RECOVERED carts (...) after 5m 12s.` `run_url` comes from
`RUN_URL` and is `null` when unset. Details are the failed checks' messages; alerts
never contain response bodies. Webhook URLs are secrets: they are never printed,
written to history, or included in error messages, which name only the channel
and the HTTP status or error type.

### n8n workflow

`n8n/watchdog_alerts.workflow.json` is a three-node workflow:

1. **Watchdog webhook** receives the alert with `POST`.
2. **Format Slack message** checks that the body is a version 1 watchdog payload
   and builds one Slack message: a red line per failure with its details, a green
   line per recovery with its downtime, and a link to the run.
3. **Post to Slack** sends that message to a Slack incoming webhook.

The webhook responds only after the last node finishes. If the payload is not a
watchdog alert or Slack rejects the message, n8n answers with an error, so the
watchdog falls back to `SLACK_WEBHOOK_URL` instead of losing the alert.

Set it up in n8n (cloud or self-hosted):

1. Create a [Slack incoming webhook](https://api.slack.com/messaging/webhooks)
   for the channel that should receive alerts.
2. In n8n, create a workflow and choose **Import from File**, then pick
   `n8n/watchdog_alerts.workflow.json`.
3. Open **Post to Slack** and replace the placeholder URL with your Slack webhook URL.
4. Open **Watchdog webhook** and change the path from `api-contract-watchdog` to a
   long random value. The webhook has no other authentication, so its URL is the secret.
5. Save and activate (publish) the workflow, then copy the webhook's **Production URL**
   into `N8N_WEBHOOK_URL` in `.env`.
6. Send the sample alert from the repository root:

   ```powershell
   .\.venv\Scripts\python.exe scripts\send_example_alert.py
   ```

   It prints `n8n accepted the example alert.` and Slack shows one failure (`carts`)
   and one recovery (`users`). The script prints only the result, never the URL.

`n8n/example_payload.json` is that sample alert, generated by the watchdog's own
payload builder. Tests check that it still matches the alert format, and they run
the workflow's JavaScript with Node.js when it is installed.

### Checker configuration

The command loads `.env` from the current working directory. Existing environment
variables take precedence. These settings are active:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `DUMMYJSON_BASE_URL` | `https://dummyjson.com` | HTTP/HTTPS base URL, optionally including a path; no query, fragment, or embedded credentials. |
| `REQUEST_TIMEOUT_SECONDS` | `10` | Network inactivity timeout; positive, finite seconds. |
| `MAX_RESPONSE_TIME_MS` | `2000` | Completed-response limit; positive, finite milliseconds. |
| `STATE_DIR` | `data` | Directory for baselines, run state, and run history; a relative path is resolved from the working directory. |
| `N8N_WEBHOOK_URL` | Empty (unset) | n8n webhook that receives the full alert payload. |
| `SLACK_WEBHOOK_URL` | Empty (unset) | Slack incoming webhook; fallback when n8n is unset or fails. |
| `RUN_URL` | Empty (unset) | Link to this run, included in alerts; GitHub Actions will set it in Phase 9. |

Blank values count as unset. Set URLs must be valid HTTP/HTTPS URLs.

HTTPX applies the timeout separately to connection, reading, writing, and waiting
for a connection pool slot. It is not a total request-duration deadline; the
response-time check measures the completed request separately.
[HTTPX timeout documentation](https://www.python-httpx.org/advanced/timeouts/).

## Mock API

`mock_api/` is a small FastAPI server with the same four endpoints as DummyJSON,
serving the synthetic samples in `mock_api/data/`. An admin can break any
endpoint on purpose, and the watchdog then reports the failure and, once the
break is cleared, the recovery.

Start it from the repository root:

```powershell
.\.venv\Scripts\python.exe -m mock_api
```

On macOS / Linux, use `.venv/bin/python -m mock_api`. It listens on
`http://127.0.0.1:8000` and loads `.env` from the working directory. Point the
checker at it in a second terminal:

```powershell
$env:DUMMYJSON_BASE_URL = "http://127.0.0.1:8000"
.\.venv\Scripts\python.exe -m api_contract_watchdog
```

Baselines and state are keyed by full URL, so mock results never mix with
DummyJSON results in the same `STATE_DIR`.

### Breaking an endpoint

Admin routes need `Authorization: Bearer <MOCK_API_ADMIN_TOKEN>`. Without a token
configured, the data routes still work and admin routes return `503`. Tokens
shorter than 16 characters are rejected at startup. Generate one with
`python -c "import secrets; print(secrets.token_urlsafe(32))"` and put it in `.env`.

| Route | Effect |
| --- | --- |
| `PUT /admin/breaks/{endpoint}` | Break one endpoint. Body: `{"mode": "..."}`; `slow` also accepts `delay_ms`. |
| `GET /admin/breaks` | List active breaks. |
| `DELETE /admin/breaks/{endpoint}` | Clear one endpoint's break. |
| `DELETE /admin/breaks` | Clear every break. |

`{endpoint}` is `products`, `single_product`, `carts`, or `users`. Breaks target
`price` for products, `total` for carts, and `age` for users, in every item.

| Mode | Response | Watchdog result |
| --- | --- | --- |
| `remove_field` | Target field missing | `contract` and `diff` fail. |
| `type_change` | Target number sent as a string | `contract` and `diff` fail. |
| `new_null` | Target field is `null` | `contract` and `diff` fail. |
| `added_field` | Adds `mockExtra` to each item | Passes; reported in `added_fields`. |
| `server_error` | HTTP `500` with an error body | `status_code`, `contract`, and `diff` fail. |
| `invalid_json` | Malformed JSON body | `contract` fails; the diff is skipped. |
| `slow` | Correct body after `delay_ms` (default 3000, max 10000) | `response_time` fails. |

PowerShell example:

```powershell
$headers = @{ Authorization = "Bearer $env:MOCK_API_ADMIN_TOKEN" }
Invoke-RestMethod -Method Put -Uri http://127.0.0.1:8000/admin/breaks/users `
  -Headers $headers -ContentType application/json -Body '{"mode": "type_change"}'
Invoke-RestMethod -Method Delete -Uri http://127.0.0.1:8000/admin/breaks -Headers $headers
```

curl example:

```bash
curl -X PUT http://127.0.0.1:8000/admin/breaks/users \
  -H "Authorization: Bearer $MOCK_API_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"mode": "type_change"}'
curl -X DELETE http://127.0.0.1:8000/admin/breaks -H "Authorization: Bearer $MOCK_API_ADMIN_TOKEN"
```

Breaks are kept in memory: restarting the server clears them, and the server
must run as a single process so every request sees the same breaks.
`GET /` lists the available endpoints, and `GET /health` returns `{"status": "ok"}`
for hosting health checks.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `MOCK_API_ADMIN_TOKEN` | Empty (admin disabled) | Bearer token for admin routes; at least 16 characters. |
| `MOCK_API_HOST` | `127.0.0.1` | Interface to listen on; hosting platforms usually need `0.0.0.0`. |
| `PORT` | `8000` | Port to listen on; most hosting platforms set this automatically. |

### Deploying to Render

`render.yaml` describes a free Render web service for the mock API.

1. In the [Render dashboard](https://dashboard.render.com/), choose
   **New > Blueprint** and connect this GitHub repository's `main` branch.
2. When prompted, enter a generated `MOCK_API_ADMIN_TOKEN`. Keep a copy in your
   local `.env`; Render stores the deployed copy.
3. Apply the Blueprint. When the deploy finishes, open
   `https://<service>.onrender.com/health` to confirm it returns `{"status": "ok"}`.
4. Put that base URL in `MOCK_API_BASE_URL` in `.env`.

Free Render services sleep after a period without traffic, and the first request
afterwards can take far longer than `MAX_RESPONSE_TIME_MS`. Wake the service with
`/health` before a demo, or expect a `response_time` failure on the first run.
Redeploys and restarts clear active breaks.

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
| `api_contract_watchdog/alerts.py` | Builds alert payloads and delivers them to n8n, falling back to Slack. |
| `api_contract_watchdog/storage.py` | Atomic file replacement shared by the baseline and state stores. |
| `api_contract_watchdog/contracts/` | Shared validation rules, products, carts, users, and endpoint-to-model mapping. |
| `tests/` | Offline setup, contract, settings, checker, CLI, diff, baseline, tracking, history, alert, n8n workflow, and mock API tests; `fixtures/` holds synthetic JSON samples. |
| `mock_api/` | FastAPI demo server with admin-controlled breaks; `data/` holds the synthetic responses it serves. |
| `data/` | Local response baselines and run history; contents are ignored by Git. |
| `n8n/` | Importable alert workflow and a sample alert payload. |
| `scripts/` | Demo helpers; `send_example_alert.py` sends the sample alert to n8n. |
| `status/` | Status page files, starting in Phase 9. |
| `docs/` | Demo screenshots and supporting documentation. |
| `.github/workflows/` | GitHub Actions workflows, starting in Phase 9. |
| `requirements.txt` | Exact versions for the project's direct dependencies. |
| `pyproject.toml` | Pytest configuration, including filters for two known Starlette test-client warnings. |
| `.env.example` | Configuration template with empty secret values. |
| `render.yaml` | Render Blueprint for deploying the mock API. |
| `.gitignore` | Excludes credentials, the virtual environment, and generated files. |

Empty folders use `.gitkeep` placeholders so Git can retain them.

## Configuration and secrets

`.env.example` includes active checker, alert, and mock API settings. The checker
reads the seven variables listed under checker configuration; the mock API reads
`MOCK_API_ADMIN_TOKEN`, `MOCK_API_HOST`, and `PORT`. The default
`data/` directory is ignored by Git; if you choose another state directory inside
the repository, add that directory to `.gitignore` too.
Keep real credentials in `.env` locally and in GitHub Actions secrets for CI.
No accounts or GitHub Secrets are required for Phase 8: without webhooks, alerts
are skipped. To receive them, import the n8n workflow described under alerts, or
use a [Slack incoming webhook](https://api.slack.com/messaging/webhooks) directly,
and put the URL in `.env`.

Later phases will provide exact setup steps for these GitHub Secrets:

- `MOCK_API_BASE_URL`: the deployed mock API's base URL.
- `MOCK_API_ADMIN_TOKEN`: the mock server's admin token, used for demo controls.
- `N8N_WEBHOOK_URL`: the n8n production webhook URL.
- `SLACK_WEBHOOK_URL`: the fallback Slack incoming webhook URL.

A deployed mock API also needs `MOCK_API_ADMIN_TOKEN` and `MOCK_API_HOST=0.0.0.0`
in its hosting environment. Do not put tokens or webhook URLs in source code or screenshots.
