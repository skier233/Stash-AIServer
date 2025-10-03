import pathlib
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stash_ai_server.actions.models import ContextRule, ContextInput


def make_ctx(*, detail: bool = False, selected: list[str] | None = None, visible: list[str] | None = None):
    return ContextInput(
        page='scenes',
        entityId=None,
        isDetailView=detail,
        selectedIds=selected or [],
        visibleIds=visible,
    )


def test_multi_requires_selection():
    rule = ContextRule(pages=['scenes'], selection='multi')
    assert rule.matches(make_ctx(selected=['1']))
    assert not rule.matches(make_ctx(selected=[]))
    assert not rule.matches(make_ctx(detail=True, selected=['1']))


def test_none_triggers_when_nothing_selected():
    rule = ContextRule(pages=['scenes'], selection='none')
    assert rule.matches(make_ctx(selected=[]))
    assert not rule.matches(make_ctx(selected=['2']))


def test_page_requires_visible_ids_and_no_selection():
    rule = ContextRule(pages=['scenes'], selection='page')
    assert rule.matches(make_ctx(selected=[], visible=['10', '11']))
    assert not rule.matches(make_ctx(selected=['10'], visible=['10', '11']))
    assert not rule.matches(make_ctx(selected=[], visible=None))


def test_all_behaves_like_none():
    rule = ContextRule(pages=['scenes'], selection='all')
    assert rule.matches(make_ctx(selected=[]))
    assert not rule.matches(make_ctx(selected=['2']))


def test_single_only_for_detail():
    rule = ContextRule(pages=['scenes'], selection='single')
    assert rule.matches(make_ctx(detail=True, selected=[]))
    assert not rule.matches(make_ctx(detail=False, selected=['1']))


def test_both_allows_any_library_state():
    rule = ContextRule(pages=['scenes'], selection='both')
    assert rule.matches(make_ctx(selected=['1']))
    assert rule.matches(make_ctx(selected=[]))
    assert not rule.matches(make_ctx(detail=True, selected=[]))
