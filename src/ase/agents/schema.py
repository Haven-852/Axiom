_TYPES = {
    "string": str,
    "object": dict,
    "array": list,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def validate_payload(payload: dict, schema: dict, *, output: bool = False) -> None:
    from ase.errors import AgentOutputValidationError, InputValidationError

    error_type = AgentOutputValidationError if output else InputValidationError
    if not isinstance(payload, dict):
        raise error_type("payload must be an object")
    if "properties" in schema or "required" in schema:
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
    else:
        properties = schema
        required = list(schema)
    for key in required:
        if key not in payload:
            raise error_type(f"missing {key}")
    for key, spec in properties.items():
        if key not in payload or not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if expected is None:
            continue
        python_type = _TYPES.get(expected)
        if python_type is not None and not isinstance(payload[key], python_type):
            raise error_type(f"{key} must be {expected}")
        if expected == "integer" and isinstance(payload[key], bool):
            raise error_type(f"{key} must be integer")
