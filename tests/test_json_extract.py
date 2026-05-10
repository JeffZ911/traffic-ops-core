"""Unit tests for the JSON extractor used by Outline / QA / Selector."""

from __future__ import annotations

import pytest

from src.agents._json_extract import extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_json_with_whitespace():
    assert extract_json("  \n  {\"a\": 1}  \n") == {"a": 1}


def test_markdown_fence():
    assert extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_markdown_fence_no_lang():
    assert extract_json("```\n{\"a\": 1}\n```") == {"a": 1}


def test_json_embedded_in_prose():
    text = "Here is the result:\n{\"a\": 1, \"b\": [\"x\"]}\nthat's all."
    assert extract_json(text) == {"a": 1, "b": ["x"]}


def test_braces_inside_string():
    assert extract_json('{"k": "value with } inside"}') == {"k": "value with } inside"}


def test_nested_object():
    assert extract_json('{"a": {"b": {"c": 1}}, "d": 2}') == {"a": {"b": {"c": 1}}, "d": 2}


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("just plain text with no JSON")


def test_unbalanced_raises():
    with pytest.raises(ValueError):
        extract_json('{"a": 1')


def test_escaped_quote_in_string():
    assert extract_json('{"k": "she said \\"hi\\""}') == {"k": 'she said "hi"'}
