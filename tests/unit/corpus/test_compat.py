"""S5-D8 compatibility rule (semantic-version tuples, behaviour packages per S5b-D6)."""

from __future__ import annotations

import pytest

from version_aware_rag.corpus.compat import is_compatible, parse_version, series_of

PACKAGES = {"1.2": "1.2.2", "1.3": "1.3.2", "1.4": "1.4.5", "1.5": "1.5.6", "2.0": "2.0.1"}


def test_parse_version_uses_integer_tuples() -> None:
    assert parse_version("1.10.0") > parse_version("1.9.0")  # strings would say the opposite
    assert parse_version("2.0.1") == (2, 0, 1)
    assert series_of("1.4.2") == "1.4"


@pytest.mark.parametrize("bad", ["1.4", "v1.4.5", "1.4.5.0", "", "1.x.0"])
def test_parse_version_rejects_non_semver(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_version(bad)


def test_same_series_is_always_compatible() -> None:
    assert is_compatible("1.4", "documentation", None, "1.4", PACKAGES)
    assert is_compatible("1.4", "changelog", "1.4.2", "1.4", PACKAGES)


def test_documentation_is_compatible_only_with_its_own_series() -> None:
    assert not is_compatible("1.4", "documentation", None, "1.5", PACKAGES)
    assert not is_compatible("1.5", "documentation", None, "1.4", PACKAGES)


@pytest.mark.parametrize(
    ("introduced_in", "chunk_series", "target", "expected"),
    [
        ("1.4.2", "1.4", "1.5", True),  # plan §4.6: a 1.4.2 note fits 1.4, 1.5 and 2.0
        ("1.4.2", "1.4", "2.0", True),
        ("1.4.2", "1.4", "1.3", False),
        ("1.4.5", "1.4", "1.4", True),
        ("2.0.1", "2.0", "1.5", False),
        ("1.2.1", "1.2", "1.3", True),
        ("1.3.2", "1.3", "1.2", False),
    ],
)
def test_change_sources_compare_against_the_target_behaviour_package(
    introduced_in: str, chunk_series: str, target: str, expected: bool
) -> None:
    assert is_compatible(chunk_series, "changelog", introduced_in, target, PACKAGES) is expected


def test_change_source_without_introduced_in_is_not_cross_version() -> None:
    assert not is_compatible("1.4", "changelog", None, "1.5", PACKAGES)


def test_unknown_target_series_is_an_error() -> None:
    with pytest.raises(ValueError):
        is_compatible("1.4", "changelog", "1.4.2", "9.9", PACKAGES)
