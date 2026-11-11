"""Compare JSON structure without retaining response values."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


JSONType = Literal["object", "array", "string", "number", "boolean", "null"]
Shape = dict[str, list[JSONType]]


class TypeChange(BaseModel):
    path: str
    previous_types: list[JSONType]
    current_types: list[JSONType]


class ShapeDiff(BaseModel):
    removed_fields: list[str] = Field(default_factory=list)
    added_fields: list[str] = Field(default_factory=list)
    type_changes: list[TypeChange] = Field(default_factory=list)
    new_nulls: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def has_breaking_changes(self) -> bool:
        return bool(self.removed_fields or self.type_changes or self.new_nulls)


def response_shape(payload: Any) -> Shape:
    observed: dict[str, set[JSONType]] = {}

    def visit(value: Any, path: str) -> None:
        if value is None:
            kind: JSONType = "null"
        elif isinstance(value, bool):
            # bool is an int subclass in Python, but a distinct JSON type.
            kind = "boolean"
        elif isinstance(value, (int, float)):
            kind = "number"
        elif isinstance(value, str):
            kind = "string"
        elif isinstance(value, dict):
            kind = "object"
        elif isinstance(value, list):
            kind = "array"
        else:
            raise TypeError("Response shape requires JSON-compatible values")
        observed.setdefault(path, set()).add(kind)
        if isinstance(value, dict):
            for key, child in value.items():
                # Quoted keys distinguish a literal 'a.b' key from nesting.
                visit(child, f"{path}[{json.dumps(key)}]")
        elif isinstance(value, list):
            for child in value:
                # Combine all items; ordering and list length are not a contract.
                visit(child, f"{path}[*]")

    visit(payload, "$")
    return {path: sorted(types) for path, types in sorted(observed.items())}


def unobserved_paths(previous: Shape, current: Shape) -> set[str]:
    prefixes = []
    for path, types in current.items():
        if "array" in types and f"{path}[*]" not in current:
            prefixes.append(f"{path}[*]")
        # A null cannot tell us anything about its former child fields.
        if types == ["null"]:
            prefixes.append(f"{path}[")
    return {
        path for path in previous
        if any(path.startswith(prefix) for prefix in prefixes)
    }


def compare_shapes(previous: Shape, current: Shape) -> ShapeDiff:
    unknown = unobserved_paths(previous, current)
    removed = set(previous) - set(current) - unknown
    added = set(current) - set(previous)
    changes = []
    new_nulls = []
    for path in sorted(set(previous) & set(current)):
        old_types = set(previous[path])
        new_types = set(current[path])
        if "null" in new_types and "null" not in old_types:
            new_nulls.append(path)
        # Array samples may show only a subset of previously observed types.
        # A formerly null-only field reveals its first known type, not a break.
        if old_types - {"null"} and new_types - old_types - {"null"}:
            changes.append(TypeChange(
                path=path,
                previous_types=previous[path],
                current_types=current[path],
            ))

    # Report the parent removal once instead of repeating every child removal.
    removed_fields = [
        path for path in sorted(removed)
        if not any(path.startswith(f"{parent}[") for parent in removed if parent != path)
    ]
    return ShapeDiff(
        removed_fields=removed_fields,
        added_fields=sorted(added),
        type_changes=changes,
        new_nulls=new_nulls,
    )


def next_baseline_shape(previous: Shape, current: Shape) -> Shape:
    updated = dict(current)
    for path in unobserved_paths(previous, current):
        updated[path] = previous[path]
    # Keep known array element types even if this sample shows fewer variants.
    for path in previous.keys() & current.keys():
        updated[path] = sorted(set(previous[path]) | set(current[path]))
    return dict(sorted(updated.items()))
