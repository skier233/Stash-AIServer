"""Simple test example for IDE test discovery debugging."""

import pytest


class TestSimpleExample:
    """Simple test class to verify IDE test discovery."""
    
    def test_simple_assertion(self):
        """Test that basic assertions work."""
        assert True
        assert 1 + 1 == 2
    
    def test_string_operations(self):
        """Test string operations."""
        text = "hello world"
        assert text.upper() == "HELLO WORLD"
        assert len(text) == 11
    
    @pytest.mark.unit
    def test_with_marker(self):
        """Test with a unit marker."""
        result = [1, 2, 3]
        assert len(result) == 3
        assert result[0] == 1
    
    def test_list_operations(self):
        """Test list operations."""
        items = [1, 2, 3, 4, 5]
        assert sum(items) == 15
        assert max(items) == 5
        assert min(items) == 1


def test_function_level_test():
    """Function-level test (not in a class)."""
    assert "pytest" in "pytest is awesome"


@pytest.mark.unit
def test_function_with_marker():
    """Function-level test with marker."""
    data = {"key": "value", "number": 42}
    assert data["key"] == "value"
    assert data["number"] == 42