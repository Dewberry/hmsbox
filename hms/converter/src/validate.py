"""Validation helpers for converter JSON-schema-based contracts."""

from pathlib import Path
import json


def validate_against_json_schema(data: object, schema: dict, path: str = "$") -> None:
    """Validate data against a minimal JSON Schema subset used by this project."""
    schema_type = schema.get("type")
    if schema_type is not None:
        if schema_type == "object" and not isinstance(data, dict):
            raise ValueError(f"{path}: expected object")
        if schema_type == "string" and not isinstance(data, str):
            raise ValueError(f"{path}: expected string")
        if schema_type == "null" and data is not None:
            raise ValueError(f"{path}: expected null")

    if "const" in schema and data != schema["const"]:
        raise ValueError(f"{path}: expected constant value {schema['const']!r}")

    if "enum" in schema and data not in schema["enum"]:
        raise ValueError(f"{path}: value {data!r} is not in enum {schema['enum']!r}")

    if "anyOf" in schema:
        errors: list[str] = []
        for option in schema["anyOf"]:
            try:
                validate_against_json_schema(data, option, path)
                break
            except ValueError as exc:
                errors.append(str(exc))
        else:
            raise ValueError(
                f"{path}: did not match anyOf options: {'; '.join(errors)}"
            )

    if isinstance(data, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in data:
                raise ValueError(f"{path}: missing required key {key!r}")

        properties = schema.get("properties", {})
        additional_properties = schema.get("additionalProperties", True)

        for key, value in data.items():
            key_path = f"{path}.{key}"

            property_names = schema.get("propertyNames")
            if property_names is not None:
                validate_against_json_schema(key, property_names, f"{path} key {key!r}")

            if key in properties:
                validate_against_json_schema(value, properties[key], key_path)
            elif additional_properties is False:
                raise ValueError(f"{path}: unexpected key {key!r}")
            elif isinstance(additional_properties, dict):
                validate_against_json_schema(value, additional_properties, key_path)


def load_json_schema(schema_path: Path) -> dict:
    """Load a JSON schema file from disk."""
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_schema_from_columns(
    columns: list[str], extraction_spec: dict[str, dict]
) -> dict:
    """Build a normalized schema payload from parquet columns using a reusable spec."""
    column_set = set(columns)
    output: dict = {}

    for output_field, rule in extraction_spec.items():
        rule_type = rule.get("type")

        if rule_type == "required_exact":
            column_name = rule["column"]
            output[output_field] = column_name if column_name in column_set else None
            continue

        if rule_type == "optional_exact":
            column_name = rule["column"]
            output[output_field] = column_name if column_name in column_set else None
            continue

        if rule_type == "identity_map_subset":
            allowed = rule["allowed"]
            output[output_field] = {
                name: name for name in allowed if name in column_set
            }
            continue

        raise ValueError(
            f"Unknown extraction rule type {rule_type!r} for field {output_field!r}"
        )

    return output


def validate_required_columns(
    schema_output: dict, extraction_spec: dict[str, dict]
) -> None:
    """Validate presence of required columns declared in extraction_spec."""
    missing: list[str] = []

    for output_field, rule in extraction_spec.items():
        if rule.get("type") != "required_exact":
            continue
        if not schema_output.get(output_field):
            missing.append(rule["column"])

    if missing:
        missing_list = ", ".join(repr(item) for item in missing)
        raise ValueError(f"Parquet missing required columns: {missing_list}")
