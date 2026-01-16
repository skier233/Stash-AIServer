"""
Tests for URL utility functions.

Tests the dockerize_localhost function with various URL formats and edge cases.
Uses property-based testing to validate URL construction and parsing correctness.
"""

import pytest
from hypothesis import given, strategies as st
from urllib.parse import urlparse

from stash_ai_server.utils.url_helpers import dockerize_localhost


class TestDockerizeLocalhost:
    """Test dockerize_localhost function."""
    
    def test_disabled_dockerization(self):
        """Test that dockerization is disabled when enabled=False."""
        url = "http://localhost:8080/path"
        result = dockerize_localhost(url, enabled=False)
        assert result == url
    
    def test_none_url(self):
        """Test handling of None URL."""
        assert dockerize_localhost(None, enabled=True) is None
        assert dockerize_localhost(None, enabled=False) is None
    
    def test_empty_url(self):
        """Test handling of empty URL."""
        assert dockerize_localhost("", enabled=True) == ""
        assert dockerize_localhost("", enabled=False) == ""
    
    def test_localhost_conversion(self):
        """Test localhost conversion to host.docker.internal."""
        test_cases = [
            ("http://localhost", "http://host.docker.internal"),
            ("http://localhost:8080", "http://host.docker.internal:8080"),
            ("https://localhost/path", "https://host.docker.internal/path"),
            ("http://localhost:3000/api/v1", "http://host.docker.internal:3000/api/v1"),
        ]
        
        for input_url, expected in test_cases:
            result = dockerize_localhost(input_url, enabled=True)
            assert result == expected
    
    def test_127_0_0_1_conversion(self):
        """Test 127.0.0.1 conversion to host.docker.internal."""
        test_cases = [
            ("http://127.0.0.1", "http://host.docker.internal"),
            ("http://127.0.0.1:8080", "http://host.docker.internal:8080"),
            ("https://127.0.0.1/path", "https://host.docker.internal/path"),
        ]
        
        for input_url, expected in test_cases:
            result = dockerize_localhost(input_url, enabled=True)
            assert result == expected
    
    def test_0_0_0_0_conversion(self):
        """Test 0.0.0.0 conversion to host.docker.internal."""
        test_cases = [
            ("http://0.0.0.0", "http://host.docker.internal"),
            ("http://0.0.0.0:8080", "http://host.docker.internal:8080"),
        ]
        
        for input_url, expected in test_cases:
            result = dockerize_localhost(input_url, enabled=True)
            assert result == expected
    
    def test_case_insensitive_localhost(self):
        """Test case-insensitive localhost detection."""
        test_cases = [
            "http://LOCALHOST",
            "http://LocalHost", 
            "http://localhost",
        ]
        
        for input_url in test_cases:
            result = dockerize_localhost(input_url, enabled=True)
            assert "host.docker.internal" in result
    
    def test_non_localhost_urls_unchanged(self):
        """Test that non-localhost URLs are not modified."""
        test_cases = [
            "http://example.com",
            "https://api.github.com/repos",
            "http://192.168.1.100:8080",
            "https://my-service.local",
            "http://localhost.example.com",  # Not exactly localhost
        ]
        
        for url in test_cases:
            result = dockerize_localhost(url, enabled=True)
            assert result == url
    
    def test_url_with_authentication(self):
        """Test URLs with username and password."""
        test_cases = [
            ("http://user@localhost:8080", "http://user@host.docker.internal:8080"),
            ("http://user:pass@localhost", "http://user:pass@host.docker.internal"),
            ("https://admin:secret@127.0.0.1:9000/api", "https://admin:secret@host.docker.internal:9000/api"),
        ]
        
        for input_url, expected in test_cases:
            result = dockerize_localhost(input_url, enabled=True)
            assert result == expected
    
    def test_url_with_query_and_fragment(self):
        """Test URLs with query parameters and fragments."""
        test_cases = [
            ("http://localhost:8080/path?param=value", "http://host.docker.internal:8080/path?param=value"),
            ("http://localhost/path#section", "http://host.docker.internal/path#section"),
            ("http://localhost:3000/api?key=123&value=test#top", "http://host.docker.internal:3000/api?key=123&value=test#top"),
        ]
        
        for input_url, expected in test_cases:
            result = dockerize_localhost(input_url, enabled=True)
            assert result == expected
    
    def test_malformed_urls(self):
        """Test handling of malformed URLs."""
        # These should not raise exceptions
        test_cases = [
            "not-a-url",
            "http://",
            "://localhost",
            "localhost:8080",  # Missing scheme
        ]
        
        for url in test_cases:
            # Should not raise an exception
            result = dockerize_localhost(url, enabled=True)
            # For malformed URLs, function should handle gracefully
            assert isinstance(result, (str, type(None)))


class TestDockerizeLocalhostProperties:
    """Property-based tests for dockerize_localhost."""
    
    @given(st.text())
    def test_disabled_returns_original(self, url):
        """Property: When disabled, function should return original URL."""
        result = dockerize_localhost(url, enabled=False)
        assert result == url
    
    @given(st.one_of(st.none(), st.just("")))
    def test_none_or_empty_handling(self, url):
        """Property: None or empty URLs should be handled gracefully."""
        result = dockerize_localhost(url, enabled=True)
        assert result == url
    
    def test_localhost_urls_always_converted(self):
        """Property: Valid localhost URLs should always be converted when enabled."""
        localhost_variants = ["localhost", "127.0.0.1", "0.0.0.0"]
        schemes = ["http", "https"]
        
        for scheme in schemes:
            for host in localhost_variants:
                url = f"{scheme}://{host}"
                result = dockerize_localhost(url, enabled=True)
                assert "host.docker.internal" in result
                assert scheme in result
    
    @given(st.integers(min_value=1, max_value=65535))
    def test_port_preservation(self, port):
        """Property: Port numbers should be preserved in conversion."""
        url = f"http://localhost:{port}"
        result = dockerize_localhost(url, enabled=True)
        assert f":{port}" in result
        assert "host.docker.internal" in result
    
    def test_url_structure_preservation(self):
        """Property: URL structure should be preserved during conversion."""
        base_url = "http://localhost:8080"
        paths = ["/", "/api", "/api/v1", "/path/to/resource"]
        queries = ["", "?param=value", "?a=1&b=2"]
        fragments = ["", "#section", "#top"]
        
        for path in paths:
            for query in queries:
                for fragment in fragments:
                    url = f"{base_url}{path}{query}{fragment}"
                    result = dockerize_localhost(url, enabled=True)
                    
                    # Parse both URLs to compare structure
                    original_parsed = urlparse(url)
                    result_parsed = urlparse(result)
                    
                    # Scheme, path, query, and fragment should be preserved
                    assert result_parsed.scheme == original_parsed.scheme
                    assert result_parsed.path == original_parsed.path
                    assert result_parsed.query == original_parsed.query
                    assert result_parsed.fragment == original_parsed.fragment
                    
                    # Hostname should be converted
                    assert result_parsed.hostname == "host.docker.internal"
                    
                    # Port should be preserved
                    assert result_parsed.port == original_parsed.port
    
    def test_round_trip_url_parsing(self):
        """Property: Converted URLs should be valid and parseable."""
        test_urls = [
            "http://localhost",
            "http://localhost:8080",
            "https://127.0.0.1:3000/api",
            "http://user:pass@0.0.0.0:9000/path?query=value#fragment"
        ]
        
        for url in test_urls:
            result = dockerize_localhost(url, enabled=True)
            
            # Result should be parseable
            parsed = urlparse(result)
            assert parsed.scheme in ["http", "https"]
            assert parsed.hostname == "host.docker.internal"
            
            # Should be able to reconstruct a valid URL
            reconstructed = parsed.geturl()
            assert reconstructed == result