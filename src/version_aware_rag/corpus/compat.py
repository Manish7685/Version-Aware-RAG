"""Version compatibility rule (RAG-M3 S5b, owner decision S5-D8; plan §2 and §4.6).

A chunk is compatible with a target docs series *v* when its own version is *v*, or when it is a
change source (``source_type == "changelog"``) whose ``introduced_in`` release is at most the
behaviour package of *v* (S5b-D6: 1.4 compares against 1.4.5). Versions are compared as integer
tuples, never as strings, so 1.10.0 sorts after 1.9.0.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(text: str) -> tuple[int, int, int]:
    """``"1.4.5"`` -> ``(1, 4, 5)``; anything else raises ``ValueError``."""
    m = _SEMVER.match(text)
    if not m:
        raise ValueError(f"not an X.Y.Z version: {text!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def series_of(release: str) -> str:
    """``"1.4.2"`` -> ``"1.4"``."""
    major, minor, _ = parse_version(release)
    return f"{major}.{minor}"


def is_compatible(
    chunk_version: str,
    source_type: str,
    introduced_in: str | None,
    target_series: str,
    behaviour_packages: Mapping[str, str],
) -> bool:
    """Plan §2: same series, or a change source introduced no later than the target's package."""
    if chunk_version == target_series:
        return True
    if source_type != "changelog" or introduced_in is None:
        return False
    target = behaviour_packages.get(target_series)
    if target is None:
        raise ValueError(f"unknown target series {target_series!r}")
    return parse_version(introduced_in) <= parse_version(target)
