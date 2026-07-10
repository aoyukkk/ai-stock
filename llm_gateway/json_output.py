from __future__ import annotations

import json
from typing import Any

from llm_gateway.exceptions import LLMSchemaValidationError


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in {"```", "```json"}:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMSchemaValidationError("Provider output is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise LLMSchemaValidationError("Provider output must be a JSON object.")
    return value


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise LLMSchemaValidationError(f"{path} must be an object.")
        missing = set(schema.get("required", [])) - set(value)
        if missing:
            raise LLMSchemaValidationError(f"{path} missing required fields: {sorted(missing)}")
        for key, child_schema in schema.get("properties", {}).items():
            if key in value and isinstance(child_schema, dict):
                validate_json_schema(value[key], child_schema, f"{path}.{key}")
    elif expected == "array":
        if not isinstance(value, list):
            raise LLMSchemaValidationError(f"{path} must be an array.")
        child_schema = schema.get("items", {})
        if isinstance(child_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(item, child_schema, f"{path}[{index}]")
    elif expected == "string" and not isinstance(value, str):
        raise LLMSchemaValidationError(f"{path} must be a string.")
    elif expected == "number" and (isinstance(value, bool) or not isinstance(value, int | float)):
        raise LLMSchemaValidationError(f"{path} must be a number.")
    elif expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        raise LLMSchemaValidationError(f"{path} must be an integer.")
    elif expected == "boolean" and not isinstance(value, bool):
        raise LLMSchemaValidationError(f"{path} must be a boolean.")

    if "enum" in schema and value not in schema["enum"]:
        raise LLMSchemaValidationError(f"{path} is not an allowed value.")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise LLMSchemaValidationError(f"{path} is below minimum.")
        if "maximum" in schema and value > schema["maximum"]:
            raise LLMSchemaValidationError(f"{path} is above maximum.")
