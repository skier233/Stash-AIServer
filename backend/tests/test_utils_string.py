"""
Tests for string utility functions.

Tests the normalize_null_strings function with various input types and edge cases.
Uses property-based testing to validate behavior across many inputs.
"""

import pytest
from hypothesis import given, strategies as st

from stash_ai_server.utils.string_utils import normalize_null_strings


class TestNormalizeNullStrings:
    """Test normalize_null_strings function."""
    
    def test_simple_null_string(self):
        """Test that 'null' string is converted to None."""
        assert normalize_null_strings("null") is None
        assert normalize_null_strings("NULL") is None
        assert normalize_null_strings("Null") is None
        assert normalize_null_strings("nULL") is None
    
    def test_non_null_strings(self):
        """Test that non-null strings are preserved."""
        assert normalize_null_strings("hello") == "hello"
        assert normalize_null_strings("") == ""
        assert normalize_null_strings("null_value") == "null_value"
        assert normalize_null_strings("not null") == "not null"
    
    def test_dict_processing(self):
        """Test dictionary processing."""
        input_dict = {
            "key1": "null",
            "key2": "value",
            "key3": "NULL",
            "key4": None
        }
        expected = {
            "key1": None,
            "key2": "value", 
            "key3": None,
            "key4": None
        }
        assert normalize_null_strings(input_dict) == expected
    
    def test_list_processing(self):
        """Test list processing."""
        input_list = ["null", "value", "NULL", None, "normal"]
        expected = [None, "value", None, None, "normal"]
        assert normalize_null_strings(input_list) == expected
    
    def test_nested_structures(self):
        """Test nested dictionary and list structures."""
        input_data = {
            "level1": {
                "level2": ["null", "value", {"nested": "NULL"}]
            },
            "list": ["null", {"inner": "null"}]
        }
        expected = {
            "level1": {
                "level2": [None, "value", {"nested": None}]
            },
            "list": [None, {"inner": None}]
        }
        assert normalize_null_strings(input_data) == expected
    
    def test_non_string_types(self):
        """Test that non-string types are preserved."""
        assert normalize_null_strings(42) == 42
        assert normalize_null_strings(3.14) == 3.14
        assert normalize_null_strings(True) is True
        assert normalize_null_strings(None) is None
    
    def test_empty_containers(self):
        """Test empty containers."""
        assert normalize_null_strings({}) == {}
        assert normalize_null_strings([]) == []
    
    def test_tuple_handling(self):
        """Test that tuples are processed as sequences."""
        input_tuple = ("null", "value", "NULL")
        expected = [None, "value", None]  # Returns list, not tuple
        assert normalize_null_strings(input_tuple) == expected
    
    def test_bytes_preservation(self):
        """Test that bytes are not processed as sequences."""
        test_bytes = b"null"
        assert normalize_null_strings(test_bytes) == test_bytes


class TestNormalizeNullStringsProperties:
    """Property-based tests for normalize_null_strings."""
    
    @given(st.text())
    def test_string_processing_property(self, text):
        """Property: String processing should only convert 'null' (case-insensitive) to None."""
        result = normalize_null_strings(text)
        if text.lower() == "null":
            assert result is None
        else:
            assert result == text
    
    @given(st.dictionaries(st.text(), st.one_of(st.text(), st.none(), st.integers())))
    def test_dict_structure_preservation(self, input_dict):
        """Property: Dictionary structure should be preserved."""
        result = normalize_null_strings(input_dict)
        assert isinstance(result, dict)
        assert set(result.keys()) == set(input_dict.keys())
    
    @given(st.lists(st.one_of(st.text(), st.none(), st.integers())))
    def test_list_length_preservation(self, input_list):
        """Property: List length should be preserved."""
        result = normalize_null_strings(input_list)
        assert isinstance(result, list)
        assert len(result) == len(input_list)
    
    @given(st.one_of(st.integers(), st.floats(allow_nan=False), st.booleans()))
    def test_non_container_types_unchanged(self, value):
        """Property: Non-container types should be returned unchanged."""
        result = normalize_null_strings(value)
        assert result == value
        assert type(result) == type(value)
    
    @given(st.recursive(
        st.one_of(st.text(), st.integers(), st.none()),
        lambda children: st.one_of(
            st.lists(children),
            st.dictionaries(st.text(), children)
        ),
        max_leaves=10
    ))
    def test_recursive_processing_property(self, nested_data):
        """Property: Recursive processing should handle arbitrarily nested structures."""
        # Should not raise an exception
        result = normalize_null_strings(nested_data)
        assert result is not None or nested_data is None
        
        # If input was a container, output should be same type
        if isinstance(nested_data, dict):
            assert isinstance(result, dict)
        elif isinstance(nested_data, list):
            assert isinstance(result, list)