"""Process runtime: startup gates and control-plane resolution.

Nothing in this package is allowed to touch market data, open sockets or write
journal rows until :func:`engine.runtime.gates.run_all_gates` has passed.
"""

import os
from pathlib import Path

__all__ = ["repository_root"]

#: File used to recognise the repository root when walking up the tree.
PROJECT_MARKER: str = "pyproject.toml"

#: Environment variable that overrides root detection (useful in containers).
REPO_ROOT_ENV: str = "XAUUSD_REPO_ROOT"


def repository_root() -> Path:
    """Return the absolute path of the repository root.

    Resolution order:

    1. ``XAUUSD_REPO_ROOT`` when set (absolute or ``~``-relative).
    2. Walk upwards from this file until a directory containing
       ``pyproject.toml`` is found.
    3. Walk upwards from the current working directory as a fallback for
       installed (non-editable) layouts where ``__file__`` sits in site-packages.

    Raises:
        RuntimeError: if no directory containing ``pyproject.toml`` is found.

    """
    override = os.environ.get(REPO_ROOT_ENV)
    if override:
        return Path(override).expanduser().resolve()

    here = Path(__file__).resolve()
    for start in (here.parent, Path.cwd().resolve()):
        for base in (start, *start.parents):
            if (base / PROJECT_MARKER).is_file():
                return base

    msg = f"could not locate {PROJECT_MARKER!r} at or above {here} or {Path.cwd().resolve()}"
    raise RuntimeError(msg)
