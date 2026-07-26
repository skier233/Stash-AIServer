"""Unit tests for backend and frontend version compatibility helpers."""

import pytest

from stash_ai_server.core.compat import is_dev_version, version_satisfies


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("1.2.3", False),
        ("0.0.0", True),
        ("0.0.0+local", True),
        ("1.2.3-dev", True),
        ("1.2.3-SNAPSHOT", True),
        ("dirty-build", True),
    ],
)
def test_is_dev_version(value, expected):
    assert is_dev_version(value) is expected


@pytest.mark.parametrize(
    ("actual", "requirement", "expected"),
    [
        ("1.2.3", None, True),
        ("1.2.3", "", True),
        (None, ">=1.0.0", False),
        ("0.0.0+local", ">=999.0.0", True),
        ("1.2.3", "1.2.3", True),
        ("1.2.3", "=1.2.3", True),
        ("1.2.3", "==1.2.3", True),
        ("1.2.3", "1.2.4", False),
        ("1.2.3", ">=1.0.0, <2.0.0", True),
        ("1.2.3", ">=1.2.3 <=1.2.3", True),
        ("1.2.3", ">1.2.3", False),
        ("1.2.3", "<1.2.3", False),
        ("1.2.3", ">1.2.2", True),
        ("1.2.3", "<1.2.4", True),
        ("2.0.0", ">=1.0.0 <2.0.0", False),
        ("1.0.0rc1", "<1.0.0", True),
        ("not-a-version", ">=1.0.0", False),
        ("1.2.3", ">=not-a-version", False),
        ("1.2.3", ">=", True),
        ("1.2.3", " ,  ", True),
    ],
)
def test_version_satisfies(actual, requirement, expected):
    assert version_satisfies(actual, requirement) is expected
