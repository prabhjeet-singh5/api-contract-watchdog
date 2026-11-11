"""Attach a shape diff and update the baseline only after a passing check."""

from .baselines import Baseline, BaselineError, BaselineStore
from .diff import compare_shapes, next_baseline_shape, response_shape
from .results import CheckOutcome, EndpointResult


def track_result(result: EndpointResult, store: BaselineStore) -> EndpointResult:
    if not result.payload_available:
        result.checks.extend([
            CheckOutcome(check_type="diff", status="skipped", message="No JSON body to compare."),
            CheckOutcome(check_type="baseline", status="skipped", message="Baseline left unchanged."),
        ])
        return result

    current = response_shape(result.payload)
    try:
        previous = store.load(result.url)
    except BaselineError as exc:
        result.checks.extend([
            CheckOutcome(check_type="diff", status="skipped", message="Baseline is unavailable."),
            CheckOutcome(check_type="baseline", status="failed", message=str(exc)),
        ])
        return result

    if previous is None:
        result.checks.append(CheckOutcome(
            check_type="diff", status="skipped", message="No previous baseline to compare."
        ))
    else:
        result.diff = compare_shapes(previous.shape, current)
        result.checks.append(CheckOutcome(
            check_type="diff",
            status="failed" if result.diff.has_breaking_changes else "passed",
            message=(
                f"Removed: {len(result.diff.removed_fields)}; added: {len(result.diff.added_fields)}; "
                f"type changes: {len(result.diff.type_changes)}; new nulls: {len(result.diff.new_nulls)}."
            ),
        ))

    if not result.passed:
        result.checks.append(CheckOutcome(
            check_type="baseline", status="skipped", message="Failed response; baseline left unchanged."
        ))
        return result

    baseline = Baseline(
        endpoint=result.endpoint,
        url=result.url,
        saved_at=result.checked_at,
        shape=next_baseline_shape(previous.shape, current) if previous else current,
    )
    try:
        store.save(baseline)
    except BaselineError as exc:
        result.checks.append(CheckOutcome(
            check_type="baseline", status="failed", message=str(exc)
        ))
    else:
        result.checks.append(CheckOutcome(
            check_type="baseline", status="passed", message="Last good response shape saved."
        ))
    return result
