from __future__ import annotations

from types import SimpleNamespace

import pytest

from stash_ai_server.utils import stash_api as stash_api_module


def make_api(interface=None):
    api = object.__new__(stash_api_module.StashAPI)
    api.tag_id_cache = {}
    api.tag_name_cache = {}
    api.stash_interface = interface
    api.stash_url = ""
    api.api_key = None
    api._effective_url = None
    return api


class InterfaceStub:
    def __init__(self):
        self.calls = []
        self.tags = {}
        self.images = []
        self.scenes = []
        self.scene = None
        self.markers = []

    def find_tag(self, value, create=False):
        self.calls.append(("find_tag", value, create))
        return self.tags.get(value)

    def create_tag(self, payload):
        self.calls.append(("create_tag", payload))
        return {"id": 42}

    def find_tags(self, **kwargs):
        self.calls.append(("find_tags", kwargs))
        return [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}]

    def update_images(self, payload):
        self.calls.append(("update_images", payload))

    def find_images(self, **kwargs):
        self.calls.append(("find_images", kwargs))
        return self.images

    def find_scenes(self, **kwargs):
        self.calls.append(("find_scenes", kwargs))
        return self.scenes

    def find_scene(self, **kwargs):
        self.calls.append(("find_scene", kwargs))
        return self.scene

    def update_scenes(self, payload):
        self.calls.append(("update_scenes", payload))

    def destroy_markers(self, marker_ids):
        self.calls.append(("destroy_markers", marker_ids))

    def find_scene_markers(self, **kwargs):
        self.calls.append(("find_scene_markers", kwargs))
        return self.markers

    def create_scene_marker(self, payload):
        self.calls.append(("create_scene_marker", payload))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", ""),
        (12, 12),
        ("/scene/1?x=1", "/scene/1?x=1"),
        ("http://stash:9999/scene/1?x=1", "/scene/1?x=1"),
        ("https://stash:9999", "/"),
    ],
)
def test_to_relative_path(value, expected):
    assert stash_api_module._to_relative_path(value) == expected


def test_refresh_configuration_disables_client_without_url(monkeypatch):
    values = {"STASH_URL": "", "STASH_API_KEY": "key"}
    monkeypatch.setattr(stash_api_module, "sys_get", values.get)
    api = make_api(InterfaceStub())
    api.tag_id_cache["old"] = 1
    api.tag_name_cache[1] = "old"

    api.refresh_configuration()

    assert api.stash_url == ""
    assert api.api_key == "key"
    assert api.stash_interface is None
    assert api.tag_id_cache == {}
    assert api.tag_name_cache == {}


def test_refresh_configuration_builds_effective_client_and_clears_caches(monkeypatch):
    values = {"STASH_URL": "http://localhost:9999", "STASH_API_KEY": "key"}
    interface = InterfaceStub()
    monkeypatch.setattr(stash_api_module, "sys_get", values.get)
    monkeypatch.setattr(
        stash_api_module,
        "dockerize_localhost",
        lambda url, enabled: "http://host.docker.internal:9999",
    )
    monkeypatch.setattr(
        stash_api_module, "_construct_stash_interface", lambda url, key: interface
    )
    api = make_api()
    api.tag_id_cache["old"] = 1

    api.refresh_configuration()

    assert api.stash_url == "http://localhost:9999"
    assert api._effective_url == "http://host.docker.internal:9999"
    assert api.api_key == "key"
    assert api.stash_interface is interface
    assert api.tag_id_cache == {}


def test_refresh_configuration_preserves_previous_client_on_constructor_error(monkeypatch):
    values = {"STASH_URL": "http://new", "STASH_API_KEY": "new-key"}
    previous = InterfaceStub()
    monkeypatch.setattr(stash_api_module, "sys_get", values.get)
    monkeypatch.setattr(stash_api_module, "dockerize_localhost", lambda url, enabled: url)
    monkeypatch.setattr(
        stash_api_module,
        "_construct_stash_interface",
        lambda *args: (_ for _ in ()).throw(RuntimeError("invalid")),
    )
    api = make_api(previous)
    api.stash_url = "http://old"
    api.api_key = "old-key"

    api.refresh_configuration()

    assert api.stash_interface is previous
    assert api.stash_url == "http://old"
    assert api.api_key == "old-key"


def test_fetch_tag_id_uses_cache_finds_and_creates_tags():
    interface = InterfaceStub()
    interface.tags = {
        "existing": {"id": "3"},
        "created-root": {"id": 4},
        "existing-child": {"id": 5},
    }
    api = make_api(interface)
    api.tag_id_cache["cached"] = 2

    assert api.fetch_tag_id("cached") == 2
    assert api.fetch_tag_id("missing") is None
    assert api.fetch_tag_id("existing") == 3
    assert api.fetch_tag_id("created-root", create_if_missing=True) == 4
    added = {}
    assert (
        api.fetch_tag_id(
            "new-child",
            parent_id=9,
            create_if_missing=True,
            use_cache=False,
            add_to_cache=added,
        )
        == 42
    )
    assert api.fetch_tag_id(
        "existing-child", parent_id=9, create_if_missing=True, use_cache=False
    ) == 5
    assert added == {"new-child": 42}
    assert api.tag_name_cache[42] == "new-child"


def test_tag_lookup_helpers_cache_results_and_handle_errors():
    interface = InterfaceStub()
    interface.tags = {7: {"id": 7, "name": "Seven"}}
    api = make_api(interface)

    assert api.get_tags_with_parent(1) == {"One": 1, "Two": 2}
    assert api.get_stash_tag_name(7) == "Seven"
    assert api.get_stash_tag_name(7) == "Seven"
    assert api.get_stash_tag_name(8) is None

    interface.find_tag = lambda value, create=False: (_ for _ in ()).throw(
        RuntimeError("failed")
    )
    assert api.get_stash_tag_name(9) is None


@pytest.mark.asyncio
async def test_image_tag_mutations_sync_and_async():
    interface = InterfaceStub()
    api = make_api(interface)

    api.add_tags_to_images([1], [2])
    api.remove_tags_from_images([1], [3])
    await api.add_tags_to_images_async([4], [5])
    await api.remove_tags_from_images_async([4], [6])

    assert [call[1] for call in interface.calls] == [
        {"ids": [1], "tag_ids": {"ids": [2], "mode": "ADD"}},
        {"ids": [1], "tag_ids": {"ids": [3], "mode": "REMOVE"}},
        {"ids": [4], "tag_ids": {"ids": [5], "mode": "ADD"}},
        {"ids": [4], "tag_ids": {"ids": [6], "mode": "REMOVE"}},
    ]


def test_get_image_paths_handles_missing_client_malformed_rows_and_errors():
    api = make_api()
    assert api.get_image_paths([1]) == {}

    interface = InterfaceStub()
    interface.images = [
        {"id": "1", "files": [{"path": "/one.jpg"}]},
        {"id": "bad", "files": []},
        {},
    ]
    api.stash_interface = interface
    assert api.get_image_paths([1, 2]) == {1: "/one.jpg"}

    interface.find_images = lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("failed")
    )
    assert api.get_image_paths([1]) == {}


def test_get_image_paths_and_tags_normalizes_partial_payloads():
    api = make_api()
    assert api.get_image_paths_and_tags([1]) == {}

    interface = InterfaceStub()
    interface.images = [
        {
            "id": "1",
            "files": [{"path": "/one.jpg"}],
            "tags": [{"id": "2"}, {"id": "bad"}, {}],
        },
        {"id": 2, "files": [], "tags": None},
        {"id": "bad", "files": [{"path": "/bad"}]},
    ]
    api.stash_interface = interface

    assert api.get_image_paths_and_tags([1, 2]) == {
        1: {"path": "/one.jpg", "tag_ids": [2]},
        2: {"path": None, "tag_ids": []},
    }

    interface.find_images = lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("failed")
    )
    assert api.get_image_paths_and_tags([1]) == {}


@pytest.mark.asyncio
async def test_async_image_read_wrappers_delegate_to_sync_methods(monkeypatch):
    api = make_api()
    monkeypatch.setattr(api, "get_image_paths", lambda ids: {1: "path"})
    monkeypatch.setattr(
        api, "get_image_paths_and_tags", lambda ids: {1: {"path": "path"}}
    )
    monkeypatch.setattr(api, "get_all_images", lambda: ["1"])

    assert await api.get_image_paths_async([1]) == {1: "path"}
    assert await api.get_image_paths_and_tags_async([1]) == {1: {"path": "path"}}
    assert await api.get_all_images_async() == ["1"]


def test_get_all_images_and_scenes_handle_success_missing_clients_and_errors():
    api = make_api()
    assert api.get_all_images() == []
    assert api.get_all_scenes() == []

    interface = InterfaceStub()
    interface.images = [{"id": 1}, {"missing": 2}]
    interface.scenes = [{"id": 3}, {"missing": 4}]
    api.stash_interface = interface
    assert api.get_all_images() == ["1"]
    assert api.get_all_scenes() == ["3"]

    interface.find_images = lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("images")
    )
    interface.find_scenes = lambda **kwargs: (_ for _ in ()).throw(
        RuntimeError("scenes")
    )
    assert api.get_all_images() == []
    assert api.get_all_scenes() == []


@pytest.mark.asyncio
async def test_scene_read_wrapper_and_metadata_paths(monkeypatch):
    api = make_api()
    assert api.get_scene_path_and_tags_and_duration(1) == (None, [], None)

    interface = InterfaceStub()
    api.stash_interface = interface
    assert api.get_scene_path_and_tags_and_duration(1) == (None, [], None)
    interface.scene = {
        "files": [{"path": "/video.mp4", "duration": 12.5}],
        "tags": [{"id": 3}, {}],
    }
    assert api.get_scene_path_and_tags_and_duration(1) == (
        "/video.mp4",
        [3],
        12.5,
    )

    monkeypatch.setattr(
        api,
        "get_scene_path_and_tags_and_duration",
        lambda scene_id: ("path", [1], 2),
    )
    assert await api.get_scene_path_and_tags_and_duration_async(1) == (
        "path",
        [1],
        2,
    )


class PaginatedInterface:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def find_scenes(self, *, filter, **kwargs):
        self.calls.append((filter, kwargs))
        return self.pages.get((filter["per_page"], filter["page"]), [])


def test_fetch_scenes_by_tag_paginated_normalizes_urls_and_probes():
    scene = {
        "id": 1,
        "paths": {
            "screenshot": "http://stash:9999/scene/1/screenshot?x=1",
            "preview": "/scene/1/preview",
        },
        "image_path": "http://stash:9999/image/1",
        "files": [{"path": "http://stash:9999/media/video.mp4"}],
    }
    interface = PaginatedInterface(
        {
            (2, 1): [scene, {"id": 2}],
            (2, 2): [{"id": 3}, {"id": 4}],
            (1, 3): [{"id": 5}],
        }
    )
    api = make_api(interface)

    scenes, total, has_more = api.fetch_scenes_by_tag_paginated(
        tag_id=9, offset=-2, limit=2
    )

    assert [item["id"] for item in scenes] == [1, 2]
    assert total == 6
    assert has_more is True
    assert scenes[0]["paths"]["screenshot"] == "/scene/1/screenshot?x=1"
    assert scenes[0]["image_path"] == "/image/1"
    assert scenes[0]["files"][0]["path"] == "/media/video.mp4"


def test_fetch_scenes_by_tag_paginated_handles_partial_pages_invalid_limit_and_errors():
    api = make_api(PaginatedInterface({(2, 2): [{"id": 3}]}))
    assert api.fetch_scenes_by_tag_paginated(1, 2, 2) == (
        [{"id": 3, "paths": {}}],
        3,
        False,
    )
    assert api.fetch_scenes_by_tag_paginated(1, 0, 0) == ([], 0, False)

    api.stash_interface = SimpleNamespace(
        find_scenes=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("failed"))
    )
    assert api.fetch_scenes_by_tag_paginated(1, 0, 2) == ([], 0, False)


@pytest.mark.asyncio
async def test_scene_tag_mutations_and_async_wrappers():
    interface = InterfaceStub()
    api = make_api(interface)

    api.add_tags_to_scene(1, [])
    api.remove_tags_from_scene(1, [])
    api.add_tags_to_scene(1, [2])
    api.remove_tags_from_scene(1, [3])
    await api.add_tags_to_scene_async(4, [5])
    await api.remove_tags_from_scene_async(4, [6])

    updates = [call[1] for call in interface.calls if call[0] == "update_scenes"]
    assert updates == [
        {"ids": [1], "tag_ids": {"ids": [2], "mode": "ADD"}},
        {"ids": [1], "tag_ids": {"ids": [3], "mode": "REMOVE"}},
        {"ids": [4], "tag_ids": {"ids": [5], "mode": "ADD"}},
        {"ids": [4], "tag_ids": {"ids": [6], "mode": "REMOVE"}},
    ]


@pytest.mark.asyncio
async def test_marker_operations_cover_empty_matches_and_async_wrappers():
    interface = InterfaceStub()
    api = make_api(interface)

    api.destroy_markers_with_tags(1, [2])
    interface.markers = [{"id": 10}, {"id": 11}]
    api.destroy_markers_with_tags(1, [2])
    api.create_scene_markers(1, {(2, "Tag"): [(1.0, 3.0), (4.0, 5.0)]})
    await api.destroy_scene_markers_async([12])
    await api.destroy_markers_with_tags_async(1, [2])
    await api.create_scene_markers_async(2, {(3, "Other"): [(0.0, 1.0)]})

    assert ("destroy_markers", [10, 11]) in interface.calls
    assert ("destroy_markers", [12]) in interface.calls
    created = [call[1] for call in interface.calls if call[0] == "create_scene_marker"]
    assert created[0] == {
        "scene_id": 1,
        "seconds": 1.0,
        "end_seconds": 3.0,
        "primary_tag_id": 2,
        "tag_ids": [2],
        "title": "Tag",
    }
    assert len(created) == 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("REPLACE_WITH_API_KEY", False),
        ("key", True),
    ],
)
def test_have_valid_api_key(value, expected):
    assert stash_api_module._have_valid_api_key(value) is expected


def test_construct_stash_interface_parses_urls_and_api_keys(monkeypatch):
    captured = []

    class CapturingInterface:
        def __init__(self, connection):
            captured.append(connection)

    monkeypatch.setattr(stash_api_module, "StashInterface", CapturingInterface)

    stash_api_module._construct_stash_interface("//localhost:9999", "key")
    stash_api_module._construct_stash_interface("localhost", None)
    stash_api_module._construct_stash_interface("https://example.com", "")
    stash_api_module._construct_stash_interface("http://example.com:bad", None)

    assert captured == [
        {"Scheme": "http", "Host": "localhost", "Port": 9999, "ApiKey": "key"},
        {"Scheme": "http", "Host": "localhost", "Port": 80},
        {"Scheme": "https", "Host": "example.com", "Port": 443},
        {"Scheme": "http", "Host": "example.com", "Port": 80},
    ]


def test_global_singleton_proxy_and_refresh_handler(monkeypatch):
    created = []

    class FakeAPI:
        def __init__(self):
            self.value = 7
            self.refreshed = False
            created.append(self)

        def refresh_configuration(self):
            self.refreshed = True

    monkeypatch.setattr(stash_api_module, "StashAPI", FakeAPI)
    monkeypatch.setattr(stash_api_module, "_stash_api_instance", None)

    first = stash_api_module.get_stash_api()
    second = stash_api_module.get_stash_api()
    assert first is second
    assert stash_api_module.StashAPIProxy().value == 7

    monkeypatch.setattr(stash_api_module, "stash_api", first)
    stash_api_module._refresh_stash_api()
    assert first.refreshed is True
