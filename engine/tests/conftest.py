"""Shared pytest configuration.

Provides the platform gate that keeps ``posix_only`` tests off Windows runners,
which matters because the engine targets Windows as a supported dev host.
"""

import sys
from collections.abc import Iterable

import pytest

__all__ = ["pytest_collection_modifyitems"]


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: Iterable[pytest.Item],
) -> None:
    """Skip any test marked ``posix_only`` when running on Windows.

    Registration of the marker itself lives in ``pyproject.toml``; this hook is
    what makes the marker actually behave differently per platform.
    """
    if not sys.platform.startswith("win"):
        return

    skip_on_windows = pytest.mark.skip(reason="posix_only test is unsupported on Windows")
    for item in items:
        if "posix_only" in item.keywords:
            item.add_marker(skip_on_windows)
