import pytest  # type: ignore[import]

from stash_ai_server.utils.url_helpers import dockerize_localhost


@pytest.mark.parametrize(
    "url,enabled,expected",
    [
        ("http://localhost:9999/graphql", True, "http://host.docker.internal:9999/graphql"),
        ("https://127.0.0.1/api", True, "https://host.docker.internal/api"),
        ("http://0.0.0.0", True, "http://host.docker.internal"),
        ("http://example.com", True, "http://example.com"),
        ("http://localhost:9999/graphql", False, "http://localhost:9999/graphql"),
        (None, True, None),
    ],
)
def test_dockerize_localhost(url, enabled, expected):
    assert dockerize_localhost(url, enabled=enabled) == expected
