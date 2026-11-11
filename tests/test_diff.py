"""Shape comparisons use only local, JSON-compatible examples."""

import pytest

from api_contract_watchdog.diff import (
    compare_shapes,
    next_baseline_shape,
    response_shape,
)


def diff(previous, current):
    return compare_shapes(response_shape(previous), response_shape(current))


def test_shape_contains_types_not_values():
    shape = response_shape({"name": "PRIVATE", "price": 1.5, "active": True, "other": None})
    assert shape == {
        "$": ["object"],
        '$["active"]': ["boolean"],
        '$["name"]': ["string"],
        '$["other"]': ["null"],
        '$["price"]': ["number"],
    }
    assert "PRIVATE" not in str(shape)


def test_all_four_change_types_are_reported():
    result = diff(
        {"removed": 1, "price": 5.0, "title": "Demo"},
        {"added": True, "price": "5.0", "title": None},
    )
    assert result.removed_fields == ['$["removed"]']
    assert result.added_fields == ['$["added"]']
    assert result.type_changes[0].path == '$["price"]'
    assert result.type_changes[0].previous_types == ["number"]
    assert result.type_changes[0].current_types == ["string"]
    assert result.new_nulls == ['$["title"]']
    assert result.has_breaking_changes


def test_value_changes_and_whole_number_prices_do_not_change_shape():
    result = diff({"id": 1, "price": 9.99}, {"id": 2, "price": 10})
    assert result.model_dump(exclude={"has_breaking_changes"}) == {
        "removed_fields": [], "added_fields": [], "type_changes": [], "new_nulls": []
    }


def test_additions_are_informational():
    result = diff({"id": 1}, {"id": 1, "extra": {"flag": True}})
    assert result.added_fields == ['$["extra"]', '$["extra"]["flag"]']
    assert not result.has_breaking_changes


def test_removed_object_is_reported_once():
    result = diff({"address": {"city": "Demo", "nested": {"x": 1}}}, {})
    assert result.removed_fields == ['$["address"]']


def test_null_parent_does_not_report_child_fields_as_removed():
    result = diff({"address": {"city": "Demo"}}, {"address": None})
    assert result.new_nulls == ['$["address"]']
    assert result.removed_fields == []


def test_previously_null_field_can_reveal_a_type():
    result = diff({"optional": None}, {"optional": {"value": 1}})
    assert result.type_changes == []
    assert result.added_fields == ['$["optional"]["value"]']
    assert not result.has_breaking_changes


def test_arrays_use_all_items_and_ignore_order_and_length():
    previous = {"items": [{"id": 1}, {"id": 2, "extra": True}]}
    current = {"items": [{"id": 3, "extra": False}, {"id": 4}, {"id": 5}]}
    result = diff(previous, current)
    assert not result.has_breaking_changes
    assert result.added_fields == []
    assert '$["items"][*]["extra"]' in response_shape(previous)


def test_null_or_new_type_in_any_array_item_is_detected():
    result = diff(
        {"items": [{"id": 1}, {"id": 2}]},
        {"items": [{"id": 3}, {"id": None}, {"id": "4"}]},
    )
    assert result.new_nulls == ['$["items"][*]["id"]']
    assert result.type_changes[0].path == '$["items"][*]["id"]'
    assert result.type_changes[0].current_types == ["null", "number", "string"]


def test_known_array_type_subset_is_not_a_change():
    result = diff({"items": [1, "demo", None]}, {"items": [2]})
    assert not result.has_breaking_changes
    saved = next_baseline_shape(
        response_shape({"items": [1, "demo", None]}), response_shape({"items": [2]})
    )
    assert saved['$["items"][*]'] == ["null", "number", "string"]


@pytest.mark.parametrize("empty", [{"items": []}, {"items": [{"details": []}]}])
def test_empty_arrays_preserve_previous_item_shape(empty):
    previous = response_shape({"items": [{"details": [{"value": 1}]}]})
    current = response_shape(empty)
    assert not compare_shapes(previous, current).has_breaking_changes
    assert next_baseline_shape(previous, current) == previous


def test_empty_array_does_not_hide_a_real_sibling_removal():
    result = diff({"items": [{"id": 1}], "total": 1}, {"items": []})
    assert result.removed_fields == ['$["total"]']


def test_keys_with_path_punctuation_are_distinct():
    shape = response_shape({"a.b": 1, "a": {"b": 2}, "*": [], 'quote"': True})
    assert '$["a.b"]' in shape
    assert '$["a"]["b"]' in shape
    assert '$["*"]' in shape
    assert '$["quote\\\""]' in shape


def test_root_null_is_a_new_null():
    result = diff({"id": 1}, None)
    assert result.new_nulls == ["$"]
    assert result.removed_fields == []


def test_boolean_is_not_a_number():
    assert diff({"value": 1}, {"value": True}).has_breaking_changes


def test_non_json_values_are_rejected():
    with pytest.raises(TypeError):
        response_shape({"value": object()})
