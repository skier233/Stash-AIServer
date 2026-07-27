"""Focused unit tests for action registration and resolution."""

from stash_ai_server.actions.models import ActionDefinition, ContextInput, ContextRule
from stash_ai_server.actions.registry import ActionRegistry, action, collect_actions


def _definition(
    *,
    action_id: str = "example",
    selection: str = "single",
    service: str = "service-a",
) -> ActionDefinition:
    return ActionDefinition(
        id=action_id,
        label=f"{action_id}-{selection}",
        service=service,
        contexts=[ContextRule(pages=["scenes"], selection=selection)],
    )


def test_registry_lists_registered_actions_in_order():
    registry = ActionRegistry()
    first_handler = lambda *_: "first"
    second_handler = lambda *_: "second"
    first = _definition(action_id="first")
    second = _definition(action_id="second")

    registry.register(first, first_handler)
    registry.register(second, second_handler)

    assert registry.list_ids() == ["first", "second"]
    assert registry.list_all() == [first, second]
    assert registry.all_for_id("first") == [first]
    assert registry.all_for_id("missing") == []


def test_resolve_prefers_definition_matching_view_kind():
    registry = ActionRegistry()
    library = _definition(selection="multi")
    detail = _definition(selection="single")
    library_handler = lambda *_: "library"
    detail_handler = lambda *_: "detail"
    registry.register(library, library_handler)
    registry.register(detail, detail_handler)

    detail_result = registry.resolve(
        "example",
        ContextInput(page="scenes", entity_id="1", is_detail_view=True),
    )
    library_result = registry.resolve(
        "example",
        ContextInput(page="scenes", selected_ids=["1"], is_detail_view=False),
    )

    assert detail_result == (detail, detail_handler)
    assert library_result == (library, library_handler)


def test_resolve_uses_applicable_fallback_and_handles_missing_actions():
    registry = ActionRegistry()
    generic = ActionDefinition(id="generic", label="Generic", contexts=[])
    handler = lambda *_: None
    registry.register(generic, handler)

    context = ContextInput(page="scenes", entity_id="1", is_detail_view=True)

    assert registry.resolve("generic", context) == (generic, handler)
    assert registry.resolve("missing", context) is None
    assert registry.resolve(
        "generic",
        ContextInput(page="images", is_detail_view=False),
    ) == (generic, handler)


def test_resolve_returns_none_when_no_definition_is_applicable():
    registry = ActionRegistry()
    registry.register(_definition(), lambda *_: None)

    assert registry.resolve(
        "example",
        ContextInput(page="images", entity_id="1", is_detail_view=True),
    ) is None


def test_unregister_service_preserves_other_definitions_and_handlers():
    registry = ActionRegistry()
    removed = _definition(service="remove-me")
    kept = _definition(service="keep-me")
    removed_handler = lambda *_: "removed"
    kept_handler = lambda *_: "kept"
    registry.register(removed, removed_handler)
    registry.register(kept, kept_handler)

    registry.unregister_service("")
    registry.unregister_service("remove-me")

    assert registry.all_for_id("example") == [kept]
    resolved = registry.resolve(
        "example",
        ContextInput(page="scenes", entity_id="1", is_detail_view=True),
    )
    assert resolved == (kept, kept_handler)

    registry.unregister_service("keep-me")
    assert registry.list_ids() == []
    assert registry.resolve(
        "example",
        ContextInput(page="scenes", entity_id="1", is_detail_view=True),
    ) is None


def test_action_decorator_and_collection_preserve_metadata_and_binding():
    class ExampleService:
        @action(
            id="decorated",
            label="Decorated action",
            description="Runs a decorated action",
            service="declared-service",
            result_kind="dialog",
            dialog_type="details",
            contexts=[ContextRule(pages=["scenes"], selection="single")],
            input_schema={"type": "object"},
            deduplicate_submissions=False,
        )
        def run(self, *_):
            return "done"

        ignored = "value"

    service = ExampleService()
    collected = collect_actions(service)

    assert len(collected) == 1
    definition, handler = collected[0]
    assert definition.id == "decorated"
    assert definition.label == "Decorated action"
    assert definition.description == "Runs a decorated action"
    assert definition.service == "declared-service"
    assert definition.result_kind == "dialog"
    assert definition.dialog_type == "details"
    assert definition.input_schema == {"type": "object"}
    assert definition.deduplicate_submissions is False
    assert handler() == "done"
