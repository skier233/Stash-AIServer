"""
Tests for path mutation utilities.

Tests the path mapping and mutation functions with various path formats and configurations.
Uses property-based testing to validate path transformation correctness.
"""

import pytest
from unittest.mock import Mock, patch
from hypothesis import given, strategies as st

from stash_ai_server.utils.path_mutation import (
    PathMapping,
    _normalize_mode,
    _coerce_mapping,
    _coerce_mappings,
    _looks_like_windows_path,
    _normalize_slashes,
    _apply_mappings,
    mutate_path_for_plugin,
    mutate_path_for_backend,
    invalidate_path_mapping_cache,
    set_session_factory
)


class TestPathMapping:
    """Test PathMapping dataclass."""
    
    def test_path_mapping_creation(self):
        """Test PathMapping creation and immutability."""
        mapping = PathMapping(source="/old/path", target="/new/path", slash_mode="unix")
        assert mapping.source == "/old/path"
        assert mapping.target == "/new/path"
        assert mapping.slash_mode == "unix"
        
        # Should be frozen (immutable)
        with pytest.raises(AttributeError):
            mapping.source = "/different/path"


class TestNormalizeMode:
    """Test _normalize_mode function."""
    
    def test_default_mode(self):
        """Test default mode handling."""
        assert _normalize_mode(None) == "auto"
        assert _normalize_mode("") == "auto"
        assert _normalize_mode("   ") == "auto"
    
    def test_valid_modes(self):
        """Test valid mode normalization."""
        assert _normalize_mode("auto") == "auto"
        assert _normalize_mode("unix") == "unix"
        assert _normalize_mode("win") == "win"
        assert _normalize_mode("windows") == "win"  # Normalized to win
        assert _normalize_mode("unchanged") == "unchanged"
        assert _normalize_mode("keep") == "unchanged"  # Normalized to unchanged
    
    def test_case_insensitive(self):
        """Test case-insensitive mode handling."""
        assert _normalize_mode("AUTO") == "auto"
        assert _normalize_mode("Unix") == "unix"
        assert _normalize_mode("WIN") == "win"
        assert _normalize_mode("Windows") == "win"
    
    def test_invalid_modes(self):
        """Test invalid mode handling."""
        assert _normalize_mode("invalid") == "auto"
        assert _normalize_mode("random") == "auto"
        assert _normalize_mode("123") == "auto"


class TestCoerceMapping:
    """Test _coerce_mapping function."""
    
    def test_valid_mapping(self):
        """Test valid mapping coercion."""
        mapping_dict = {
            "source": "/old/path",
            "target": "/new/path",
            "slash_mode": "unix"
        }
        result = _coerce_mapping(mapping_dict)
        assert result is not None
        assert result.source == "/old/path"
        assert result.target == "/new/path"
        assert result.slash_mode == "unix"
    
    def test_alternative_keys(self):
        """Test alternative key names."""
        mapping_dict = {
            "source_path": "/old/path",
            "target_path": "/new/path"
        }
        result = _coerce_mapping(mapping_dict)
        assert result is not None
        assert result.source == "/old/path"
        assert result.target == "/new/path"
        assert result.slash_mode == "auto"  # Default
    
    def test_empty_source(self):
        """Test empty source handling."""
        mapping_dict = {
            "source": "",
            "target": "/new/path"
        }
        result = _coerce_mapping(mapping_dict)
        assert result is None
        
        mapping_dict = {
            "source": "   ",
            "target": "/new/path"
        }
        result = _coerce_mapping(mapping_dict)
        assert result is None
    
    def test_missing_target(self):
        """Test missing target handling."""
        mapping_dict = {
            "source": "/old/path"
        }
        result = _coerce_mapping(mapping_dict)
        assert result is not None
        assert result.source == "/old/path"
        assert result.target == ""  # Default empty


class TestCoerceMappings:
    """Test _coerce_mappings function."""
    
    def test_none_input(self):
        """Test None input handling."""
        result = _coerce_mappings(None)
        assert result == ()
    
    def test_list_of_dicts(self):
        """Test list of dictionary mappings."""
        mappings_list = [
            {"source": "/path1", "target": "/new1"},
            {"source": "/path2", "target": "/new2"}
        ]
        result = _coerce_mappings(mappings_list)
        assert len(result) == 2
        assert result[0].source == "/path1"
        assert result[1].source == "/path2"
    
    def test_list_of_sequences(self):
        """Test list of sequence mappings."""
        mappings_list = [
            ["/path1", "/new1", "unix"],
            ["/path2", "/new2"]
        ]
        result = _coerce_mappings(mappings_list)
        assert len(result) == 2
        assert result[0].source == "/path1"
        assert result[0].target == "/new1"
        assert result[0].slash_mode == "unix"
        assert result[1].source == "/path2"
        assert result[1].target == "/new2"
    
    def test_sorting_by_length(self):
        """Test that mappings are sorted by source length (longest first)."""
        mappings_list = [
            {"source": "/a", "target": "/new1"},
            {"source": "/a/b/c", "target": "/new2"},
            {"source": "/a/b", "target": "/new3"}
        ]
        result = _coerce_mappings(mappings_list)
        assert len(result) == 3
        assert result[0].source == "/a/b/c"  # Longest first
        assert result[1].source == "/a/b"
        assert result[2].source == "/a"      # Shortest last


class TestLooksLikeWindowsPath:
    """Test _looks_like_windows_path function."""
    
    def test_windows_drive_paths(self):
        """Test Windows drive letter paths."""
        assert _looks_like_windows_path("C:\\Users\\test") is True
        assert _looks_like_windows_path("D:/Documents") is True
        assert _looks_like_windows_path("c:\\temp") is True
        assert _looks_like_windows_path("Z:") is True
    
    def test_unc_paths(self):
        """Test UNC paths."""
        assert _looks_like_windows_path("\\\\server\\share") is True
        assert _looks_like_windows_path("\\server") is True
    
    def test_unix_paths(self):
        """Test Unix-style paths."""
        assert _looks_like_windows_path("/usr/local/bin") is False
        assert _looks_like_windows_path("/home/user") is False
        assert _looks_like_windows_path("./relative/path") is False
    
    def test_mixed_paths(self):
        """Test paths with mixed separators."""
        assert _looks_like_windows_path("C:\\Users/test") is True  # Backslash comes first
        assert _looks_like_windows_path("/mnt/c\\Users") is False  # Forward slash comes first
    
    def test_edge_cases(self):
        """Test edge cases."""
        assert _looks_like_windows_path("") is False
        assert _looks_like_windows_path("   ") is False
        assert _looks_like_windows_path("relative") is False
        assert _looks_like_windows_path("1:/invalid") is False  # Not a letter


class TestNormalizeSlashes:
    """Test _normalize_slashes function."""
    
    def test_unchanged_mode(self):
        """Test unchanged mode preserves original."""
        path = "C:\\Users/mixed\\path"
        result = _normalize_slashes(path, "unchanged")
        assert result == path
    
    def test_win_mode(self):
        """Test Windows mode converts to backslashes."""
        assert _normalize_slashes("/usr/local/bin", "win") == "\\usr\\local\\bin"
        assert _normalize_slashes("C:/Users/test", "win") == "C:\\Users\\test"
    
    def test_unix_mode(self):
        """Test Unix mode converts to forward slashes."""
        assert _normalize_slashes("C:\\Users\\test", "unix") == "/C:/Users/test"
        assert _normalize_slashes("\\\\server\\share", "unix") == "//server/share"
    
    def test_auto_mode_windows_paths(self):
        """Test auto mode with Windows-style paths."""
        assert _normalize_slashes("C:/Users/test", "auto") == "C:\\Users\\test"
        assert _normalize_slashes("D:\\Documents/file", "auto") == "D:\\Documents\\file"
    
    def test_auto_mode_unix_paths(self):
        """Test auto mode with Unix-style paths."""
        assert _normalize_slashes("/usr/local\\bin", "auto") == "/usr/local/bin"
        # relative\\path is detected as Windows-style due to backslash, so it stays as-is
        assert _normalize_slashes("relative\\path", "auto") == "relative\\path"
    
    def test_empty_path(self):
        """Test empty path handling."""
        assert _normalize_slashes("", "unix") == ""
        assert _normalize_slashes("", "win") == ""


class TestApplyMappings:
    """Test _apply_mappings function."""
    
    def test_no_mappings(self):
        """Test with no mappings."""
        result, mapping = _apply_mappings("/some/path", [])
        assert result == "/some/path"
        assert mapping is None
    
    def test_simple_mapping(self):
        """Test simple path mapping."""
        mappings = [PathMapping(source="/old", target="/new", slash_mode="unchanged")]
        result, mapping = _apply_mappings("/old/file.txt", mappings)
        assert result == "/new/file.txt"
        assert mapping is not None
        assert mapping.source == "/old"
    
    def test_no_match(self):
        """Test when no mapping matches."""
        mappings = [PathMapping(source="/other", target="/new", slash_mode="unchanged")]
        result, mapping = _apply_mappings("/some/path", mappings)
        assert result == "/some/path"
        assert mapping is None
    
    def test_first_match_wins(self):
        """Test that first matching mapping is used."""
        mappings = [
            PathMapping(source="/old/specific", target="/new1", slash_mode="unchanged"),
            PathMapping(source="/old", target="/new2", slash_mode="unchanged")
        ]
        result, mapping = _apply_mappings("/old/specific/file.txt", mappings)
        assert result == "/new1/file.txt"
        assert mapping.target == "/new1"
    
    def test_case_insensitive_windows(self):
        """Test case-insensitive matching for Windows paths."""
        mappings = [PathMapping(source="C:\\Users", target="D:\\Users", slash_mode="unchanged")]
        result, mapping = _apply_mappings("c:\\users\\test\\file.txt", mappings)
        assert result == "D:\\Users\\test\\file.txt"
        assert mapping is not None
    
    def test_slash_normalization(self):
        """Test slash normalization during mapping."""
        mappings = [PathMapping(source="/old", target="/new", slash_mode="win")]
        result, mapping = _apply_mappings("/old/path/file.txt", mappings)
        assert result == "\\new\\path\\file.txt"


class TestMutatePathFunctions:
    """Test high-level path mutation functions."""
    
    def setUp(self):
        """Set up test session factory."""
        # Mock session factory for testing
        mock_session = Mock()
        mock_session.__enter__ = Mock(return_value=mock_session)
        mock_session.__exit__ = Mock(return_value=None)
        
        def mock_session_factory():
            return mock_session
        
        set_session_factory(mock_session_factory)
        
        # Clear cache before each test
        invalidate_path_mapping_cache()
    
    @patch('stash_ai_server.utils.path_mutation._fetch_setting')
    def test_mutate_path_for_plugin(self, mock_fetch):
        """Test plugin path mutation."""
        self.setUp()
        
        # Mock plugin settings
        mock_fetch.return_value = [
            {"source": "/old/plugin", "target": "/new/plugin", "slash_mode": "unix"}
        ]
        
        result = mutate_path_for_plugin("/old/plugin/file.txt", "test_plugin")
        assert result == "/new/plugin/file.txt"
    
    @patch('stash_ai_server.utils.path_mutation._fetch_setting')
    def test_mutate_path_for_backend(self, mock_fetch):
        """Test backend path mutation."""
        self.setUp()
        
        # Mock system settings
        mock_fetch.return_value = [
            {"source": "/old/system", "target": "/new/system", "slash_mode": "unix"}
        ]
        
        result = mutate_path_for_backend("/old/system/file.txt")
        assert result == "/new/system/file.txt"
    
    def test_empty_path_handling(self):
        """Test empty path handling."""
        assert mutate_path_for_plugin("", "test_plugin") == ""
        assert mutate_path_for_backend("") == ""
        assert mutate_path_for_plugin(None, "test_plugin") is None
        assert mutate_path_for_backend(None) is None


class TestPathMutationProperties:
    """Property-based tests for path mutation."""
    
    @given(st.text())
    def test_empty_mappings_preserve_path(self, path):
        """Property: Empty mappings should preserve original path."""
        result, mapping = _apply_mappings(path, [])
        assert result == path
        assert mapping is None
    
    @given(st.text(min_size=1))
    def test_normalize_mode_always_valid(self, mode_input):
        """Property: _normalize_mode should always return a valid mode."""
        result = _normalize_mode(mode_input)
        valid_modes = {"auto", "unix", "win", "unchanged"}
        assert result in valid_modes
    
    def test_slash_normalization_idempotent(self):
        """Property: Slash normalization should be idempotent."""
        test_paths = [
            "/unix/style/path",
            "C:\\Windows\\Style\\Path",
            "/mixed\\style/path"
        ]
        
        for path in test_paths:
            for mode in ["unix", "win", "unchanged"]:
                first_pass = _normalize_slashes(path, mode)
                second_pass = _normalize_slashes(first_pass, mode)
                assert first_pass == second_pass
    
    def test_mapping_order_consistency(self):
        """Property: Mapping order should be consistent (longest source first)."""
        sources = ["/a", "/a/b/c/d", "/a/b", "/a/b/c"]
        mappings_data = [{"source": s, "target": f"/new{i}"} for i, s in enumerate(sources)]
        
        result = _coerce_mappings(mappings_data)
        
        # Should be sorted by length, descending
        lengths = [len(m.source) for m in result]
        assert lengths == sorted(lengths, reverse=True)
    
    @given(st.lists(st.text(min_size=1), min_size=1, max_size=10))
    def test_coerce_mappings_preserves_valid_sources(self, sources):
        """Property: Valid sources should be preserved in coerced mappings."""
        mappings_data = [{"source": s, "target": f"/target_{i}"} for i, s in enumerate(sources)]
        result = _coerce_mappings(mappings_data)
        
        result_sources = {m.source for m in result}
        expected_sources = {s.strip() for s in sources if s.strip()}  # Non-empty after strip
        
        assert result_sources == expected_sources