from __future__ import annotations

from urllib.parse import urlparse, urlunparse

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


def dockerize_localhost(url: str | None, *, enabled: bool) -> str | None:
    """Convert localhost-style hosts to host.docker.internal when running in docker.

    Returns the original URL if docker remapping is disabled, the URL is empty,
    or the hostname is not a loopback alias.
    """

    if not enabled or not url:
        return url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        return url

    netloc = "host.docker.internal"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"

    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        netloc = f"{auth}@{netloc}"

    return urlunparse(parsed._replace(netloc=netloc))
