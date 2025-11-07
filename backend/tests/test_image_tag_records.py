import importlib
import pathlib
import sys
import types
from typing import Any

import pytest

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class _FakeStashAPI:
    def __init__(self):
        self.stash_interface = types.SimpleNamespace(get_configuration=lambda: {"ui": {}})
        self.fetch_tag_id_calls: list[dict[str, Any]] = []
        self.fetch_tag_id_result: int | None = 1

    def fetch_tag_id(
        self,
        tag_name,
        parent_id=None,
        create_if_missing=False,
        use_cache=True,
        add_to_cache=None,
    ):
        self.fetch_tag_id_calls.append(
            {
                "tag_name": tag_name,
                "parent_id": parent_id,
                "create_if_missing": create_if_missing,
                "use_cache": use_cache,
                "add_to_cache": add_to_cache,
            }
        )
        return self.fetch_tag_id_result

    def get_tags_with_parent(self, *args, **kwargs):
        return {}

    def get_stash_tag_name(self, *args, **kwargs):
        return None

    def remove_tags_from_images(self, *args, **kwargs):
        return None

    def add_tags_to_images(self, *args, **kwargs):
        return None

    def destroy_markers_with_tags(self, *args, **kwargs):
        return None

    def create_scene_markers(self, *args, **kwargs):
        return None


@pytest.fixture
def utils_module(monkeypatch):
    fake_module = types.ModuleType("stash_ai_server.utils.stash_api")
    fake_module.stash_api = _FakeStashAPI()
    sys.modules["stash_ai_server.utils.stash_api"] = fake_module

    # Ensure dependent plugin modules reload to pick up the stub
    for name in list(sys.modules):
        if name.startswith("plugins.skier_aitagging_plugin"):
            del sys.modules[name]

    module = importlib.import_module("plugins.skier_aitagging_plugin.utils")
    return module


@pytest.mark.parametrize(
    "tags_by_category",
    [
        {"": ["Enabled", "Disabled", "disabled", "Enabled"]},
        {None: ["Enabled", "Disabled", "disabled", "Enabled"]},
    ],
)
def test_collect_image_tag_records_preserves_disabled_labels(utils_module, monkeypatch, tags_by_category):
    """Ensure labels persist even when no stash tag id is available."""

    def fake_resolver(label, config):
        if label == "Enabled":
            return 101
        return None

    module = utils_module
    monkeypatch.setattr(module, "resolve_image_tag_id_from_label", fake_resolver)
    records = module.collect_image_tag_records(tags_by_category, config=types.SimpleNamespace())

    assert None in records
    bucket = records[None]
    enabled = [entry for entry in bucket if entry.label == "Enabled"]
    disabled = [entry for entry in bucket if entry.label == "Disabled"]

    assert len(enabled) == 1
    assert enabled[0].tag_id == 101
    # Duplicate disabled labels should collapse into a single record with no tag id.
    assert len(disabled) == 1
    assert disabled[0].tag_id is None


def test_collect_image_tag_records_deduplicates_ids(utils_module, monkeypatch):
    def fake_resolver(label, config):
        return {"A": 1, "B": 1, "C": 2}.get(label)

    module = utils_module
    monkeypatch.setattr(module, "resolve_image_tag_id_from_label", fake_resolver)
    records = module.collect_image_tag_records({"category": ["A", "B", "C"]}, config=types.SimpleNamespace())

    bucket = records["category"]
    assert len(bucket) == 2  # A/B share the same id, only one entry plus C
    ids = sorted(entry.tag_id for entry in bucket)
    assert ids == [1, 2]


def test_resolve_image_tag_id_fetches_existing_when_disabled(utils_module):
    module = utils_module

    settings = types.SimpleNamespace(stash_name="Existing_AI", image_enabled=False)
    config = types.SimpleNamespace(resolve=lambda _: settings)

    module.stash_api.fetch_tag_id_result = 123

    result = module.resolve_image_tag_id_from_label("Existing", config)

    assert result == 123
    assert module.stash_api.fetch_tag_id_calls
    last_call = module.stash_api.fetch_tag_id_calls[-1]
    assert last_call["tag_name"] == "Existing_AI"
    assert last_call["create_if_missing"] is False
