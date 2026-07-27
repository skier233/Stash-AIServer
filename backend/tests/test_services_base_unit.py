"""Unit tests for remote service HTTP and readiness primitives."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from stash_ai_server.services import base


class Payload(BaseModel):
    value: int


def _attach_transport(client: base.HTTPClient, handler):
    client._client = httpx.AsyncClient(
        base_url=client.base_url,
        transport=httpx.MockTransport(handler),
    )
    return client


def test_timeout_and_text_helpers():
    timeout = httpx.Timeout(5)

    assert base._coerce_timeout(timeout) is timeout
    assert base._coerce_timeout(2).connect == 2
    assert base._coerce_timeout(None) is base._DEFAULT_TIMEOUT
    assert base._trim(None) is None
    assert base._trim("short", limit=10) == "short"
    assert base._trim("longer than limit", limit=10) == "longer ..."


def test_connectivity_probe_description_includes_available_details():
    probe = base.ConnectivityProbe(
        True,
        "ready",
        status_code=200,
        latency_ms=12.34,
        error="none",
    )

    assert probe.describe() == "ready; status=200; 12.3ms; error=none"
    assert base.ConnectivityProbe(True, "ready").describe() == "ready"


def test_http_client_validates_and_normalizes_base_urls_and_paths():
    with pytest.raises(ValueError, match="base_url is required"):
        base.HTTPClient("")

    client = base.HTTPClient("http://example.test/")

    assert client.base_url == "http://example.test"
    assert client._normalize_path("") == "/"
    assert client._normalize_path("ready") == "/ready"
    assert client._normalize_path("/ready") == "/ready"
    assert client._normalize_path("https://other.test/path") == (
        "https://other.test/path"
    )


@pytest.mark.asyncio
async def test_http_client_creates_reuses_and_closes_underlying_client():
    client = base.HTTPClient(
        "http://example.test",
        timeout=3,
        headers={"X-Test": "yes"},
        verify=False,
        follow_redirects=False,
    )

    first, second = await asyncio.gather(
        client._get_client(),
        client._get_client(),
    )

    assert first is second
    assert first.headers["User-Agent"] == "stash-ai-server-plugin/1.0"
    assert first.headers["X-Test"] == "yes"
    await client.close()
    assert client._client is None
    await client.close()


@pytest.mark.asyncio
async def test_http_methods_forward_payloads_and_decode_json():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"value": len(requests)},
            request=request,
        )

    client = _attach_transport(base.HTTPClient("http://example.test"), handler)

    assert await client.get("items", expect_json=True) == {"value": 1}
    assert await client.post(
        "/items",
        json={"name": "item"},
        expect_json=True,
    ) == {"value": 2}
    assert await client.put(
        "/items/1",
        content=b"payload",
        expect_json=True,
    ) == {"value": 3}
    response = await client.delete("/items/1")

    assert response.status_code == 200
    assert [request.method for request in requests] == [
        "GET",
        "POST",
        "PUT",
        "DELETE",
    ]
    assert json.loads(requests[1].content) == {"name": "item"}
    assert requests[2].content == b"payload"
    await client.close()


@pytest.mark.asyncio
async def test_http_client_validates_response_models_and_type_adapters():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list":
            return httpx.Response(
                200,
                json=[{"value": 1}, {"value": 2}],
                request=request,
            )
        return httpx.Response(200, json={"value": 3}, request=request)

    client = _attach_transport(base.HTTPClient("http://example.test"), handler)

    assert await client.get("/one", response_model=Payload) == Payload(value=3)
    adapter = TypeAdapter(list[Payload])
    assert await client.get("/list", response_model=adapter) == [
        Payload(value=1),
        Payload(value=2),
    ]
    await client.close()


@pytest.mark.asyncio
async def test_http_client_handles_empty_invalid_and_error_responses():
    def handler(request: httpx.Request) -> httpx.Response:
        responses = {
            "/empty": httpx.Response(204, request=request),
            "/invalid-json": httpx.Response(200, text="not-json", request=request),
            "/invalid-model": httpx.Response(
                200,
                json={"value": "not-an-integer"},
                request=request,
            ),
            "/error": httpx.Response(500, text="failure", request=request),
        }
        return responses[request.url.path]

    client = _attach_transport(base.HTTPClient("http://example.test"), handler)

    assert await client.get("/empty", expect_json=True) is None
    with pytest.raises(ValueError, match="Expected JSON payload"):
        await client.get("/empty", response_model=Payload)
    with pytest.raises(ValueError, match="not valid JSON"):
        await client.get("/invalid-json", expect_json=True)
    with pytest.raises(ValidationError):
        await client.get("/invalid-model", response_model=Payload)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get("/error")

    response = await client.request(
        "GET",
        "/error",
        raise_for_status=False,
    )
    assert response.status_code == 500
    await client.close()


@pytest.mark.asyncio
async def test_check_ready_reports_success_http_errors_and_network_errors():
    success = _attach_transport(
        base.HTTPClient("http://example.test"),
        lambda request: httpx.Response(200, request=request),
    )
    unavailable = _attach_transport(
        base.HTTPClient("http://example.test"),
        lambda request: httpx.Response(
            503,
            text="x" * 250,
            request=request,
        ),
    )

    def network_error(request: httpx.Request):
        raise httpx.ConnectError("connection failed", request=request)

    disconnected = _attach_transport(
        base.HTTPClient("http://example.test"),
        network_error,
    )

    ready_probe = await success.check_ready()
    unavailable_probe = await unavailable.check_ready()
    network_probe = await disconnected.check_ready()

    assert ready_probe.ok is True
    assert ready_probe.status == "ready"
    assert ready_probe.status_code == 200
    assert unavailable_probe.ok is False
    assert unavailable_probe.status == "status-503"
    assert unavailable_probe.status_code == 503
    assert unavailable_probe.error.endswith("...")
    assert len(unavailable_probe.error) == 200
    assert network_probe.ok is False
    assert network_probe.status == "network-error"
    assert "connection failed" in network_probe.error
    await success.close()
    await unavailable.close()
    await disconnected.close()


class FakeHTTPClient:
    def __init__(self, base_url, probes=()):
        self._base_url = base_url
        self.probes = list(probes)
        self.ready_calls = []
        self.closed = False

    @property
    def base_url(self):
        return self._base_url

    async def check_ready(self, path):
        self.ready_calls.append(path)
        return self.probes.pop(0)

    async def close(self):
        self.closed = True


class ExampleRemoteService(base.RemoteServiceBase):
    name = "example-remote"

    def __init__(self):
        self.created_clients = []
        super().__init__()

    def build_http_client(self, base_url):
        client = FakeHTTPClient(base_url)
        self.created_clients.append(client)
        return client


def test_remote_service_requires_url_and_builds_normalized_client(monkeypatch):
    service = ExampleRemoteService()
    monkeypatch.setattr(base.settings, "docker_mode", False)

    with pytest.raises(RuntimeError, match="does not define a server_url"):
        _ = service.http

    service.server_url = "http://example.test/"
    first = service.http
    assert first.base_url == "http://example.test"
    assert service.http is first

    service.server_url = "http://other.test/"
    second = service.http
    assert second.base_url == "http://other.test"
    assert second is not first
    assert first.closed is True


@pytest.mark.asyncio
async def test_remote_service_replaces_client_inside_running_loop(monkeypatch):
    service = ExampleRemoteService()
    monkeypatch.setattr(base.settings, "docker_mode", False)
    service.server_url = "http://first.test"
    first = service.http

    service.server_url = "http://second.test"
    second = service.http
    await asyncio.sleep(0)

    assert second is not first
    assert first.closed is True
    await service.close()
    assert second.closed is True
    assert service._http_client is None


@pytest.mark.asyncio
async def test_remote_service_without_url_is_local_and_ready():
    service = ExampleRemoteService()

    assert await service.ensure_remote_ready() is True
    assert service.connectivity() == "local: no server_url configured"
    assert service.connectivity_details()["state"] == "local"


@pytest.mark.asyncio
async def test_remote_service_caches_success_and_force_rechecks(monkeypatch):
    service = ExampleRemoteService()
    service.server_url = "http://example.test"
    client = FakeHTTPClient(
        service.server_url,
        [
            base.ConnectivityProbe(True, "ready", 200, latency_ms=1),
            base.ConnectivityProbe(True, "ready", 200, latency_ms=2),
        ],
    )
    service._http_client = client
    monkeypatch.setattr(
        base,
        "time",
        SimpleNamespace(monotonic=lambda: 100.0),
    )
    monkeypatch.setattr(base.settings, "docker_mode", False)

    assert await service.ensure_remote_ready() is True
    assert await service.ensure_remote_ready() is True
    assert len(client.ready_calls) == 1
    assert service.connectivity().startswith("ready: ready; status=200")
    assert service.connectivity_details()["last_ready_error"] is None

    assert await service.ensure_remote_ready(force=True) is True
    assert len(client.ready_calls) == 2


@pytest.mark.asyncio
async def test_remote_service_tracks_failure_waiting_and_recovery(monkeypatch):
    service = ExampleRemoteService()
    service.server_url = "http://example.test"
    client = FakeHTTPClient(
        service.server_url,
        [
            base.ConnectivityProbe(False, "status-503", 503, "unavailable", 2),
            base.ConnectivityProbe(True, "ready", 200, latency_ms=1),
        ],
    )
    service._http_client = client
    monkeypatch.setattr(
        base,
        "time",
        SimpleNamespace(monotonic=lambda: 200.0),
    )
    monkeypatch.setattr(base.settings, "docker_mode", False)

    assert await service.ensure_remote_ready() is False
    assert service.connectivity().startswith("unreachable: status-503")
    assert await service.ensure_remote_ready() is False
    assert service.connectivity() == "waiting: status-503; status=503; 2.0ms; error=unavailable"
    assert len(client.ready_calls) == 1

    assert await service.ensure_remote_ready(force=True) is True
    assert service.connectivity().startswith("ready:")


@pytest.mark.asyncio
async def test_remote_service_honors_recent_failure_backoff(monkeypatch):
    service = ExampleRemoteService()
    service.server_url = "http://example.test"
    client = FakeHTTPClient(service.server_url)
    service._http_client = client
    service._last_ready_failure = 95.0
    service._last_ready_error = "previous failure"
    service._next_ready_attempt = 0.0
    monkeypatch.setattr(
        base,
        "time",
        SimpleNamespace(monotonic=lambda: 100.0),
    )
    monkeypatch.setattr(base.settings, "docker_mode", False)

    assert await service.ensure_remote_ready() is False
    assert service.connectivity() == "unreachable: previous failure"
    assert client.ready_calls == []
