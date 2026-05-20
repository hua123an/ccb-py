from ccb.tools.base import validate_input


def test_validate_input_accepts_one_of_string_or_array():
    schema = {
        "type": "object",
        "properties": {
            "options": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            }
        },
    }

    assert validate_input({"options": "a,b"}, schema) == []
    assert validate_input({"options": ["a", "b"]}, schema) == []


def test_validate_input_accepts_array_of_structured_option_objects():
    schema = {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "description": {"type": "string"},
                            },
                        },
                    ]
                },
            }
        },
    }

    assert validate_input(
        {
            "options": [
                {"label": "直接修复", "value": "fix_now", "description": "继续执行"},
                "跳过",
            ]
        },
        schema,
    ) == []


def test_validate_input_reports_nested_item_errors():
    schema = {
        "type": "object",
        "properties": {
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                    },
                },
            }
        },
    }

    errors = validate_input(
        {
            "options": [
                {"label": "继续", "value": 123},
            ]
        },
        schema,
    )

    assert errors == ["Field 'options[0].value' must be a string, got int"]


def test_validate_input_reports_one_of_mismatch():
    schema = {
        "type": "object",
        "properties": {
            "options": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            }
        },
    }

    errors = validate_input({"options": 42}, schema)

    assert errors == [
        "Field 'options' does not match any allowed schema (Field 'options' must be a string, got int; Field 'options' must be an array, got int)"
    ]


def test_validate_input_rejects_non_object_top_level_input():
    schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
        },
    }

    errors = validate_input(["bad"], schema)

    assert errors == ["Input must be an object, got list"]
