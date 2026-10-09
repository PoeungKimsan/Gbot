"""Control-plane endpoint resolution for Windows, macOS and Linux.

One code path serves every supported host: the control plane is TCP on loopback
and its credentials come from the environment or a file under ``config/``.  That
keeps a single implementation for all three platforms and avoids ``AF_UNIX``
entirely, which cannot work uniformly anyway - Windows has no ``AF_UNIX`` and
macOS caps ``sun_path`` at 104 bytes.

The control plane carries privileged commands (start, stop, flatten), so the
token is mandatory and there is no insecure default.
"""

import os
from pathlib import Path
from typing import Final

from engine.runtime import repository_root

__all__ = [
    "CONFIG_DIR_ENV",
    "CONTROL_HOST",
    "CONTROL_PORT_ENV",
    "CONTROL_TOKEN_ENV",
    "CONTROL_TOKEN_FILENAME",
    "CONTROL_TOKEN_PATH",
    "DEFAULT_CONTROL_PORT",
    "MAX_PORT",
    "MIN_PORT",
    "ControlPlaneError",
    "ControlPortError",
    "ControlTokenError",
    "control_token_path",
    "get_control_endpoint",
    "get_control_port",
    "get_control_token",
]

#: Loopback only. The control plane must never be reachable off-host.
CONTROL_HOST: Final[str] = "127.0.0.1"

#: Default control-plane TCP port, overridable per host.
DEFAULT_CONTROL_PORT: Final[int] = 8765

MIN_PORT: Final[int] = 1
MAX_PORT: Final[int] = 65535

# Names of the environment variables consulted, not their values.
CONTROL_PORT_ENV: Final[str] = "XAUUSD_CONTROL_PORT"
CONTROL_TOKEN_ENV: Final[str] = "XAUUSD_CONTROL_TOKEN"  # noqa: S105 # env var name, not a value
CONFIG_DIR_ENV: Final[str] = "XAUUSD_CONFIG_DIR"

#: Repository-relative location of the control token file.
CONTROL_TOKEN_FILENAME: Final[str] = "control.token"  # noqa: S105 # file name, not a value
CONTROL_TOKEN_PATH: Final[tuple[str, str]] = ("config", CONTROL_TOKEN_FILENAME)


class ControlPlaneError(RuntimeError):
    """Base class for control-plane configuration failures."""


class ControlPortError(ControlPlaneError):
    """The configured control-port value is unusable."""


class ControlTokenError(ControlPlaneError):
    """No control token could be resolved."""


def get_control_port() -> int:
    """Return the control-plane port from ``XAUUSD_CONTROL_PORT``.

    Falls back to :data:`DEFAULT_CONTROL_PORT` when the variable is unset or
    empty, so hosts that never touch the control plane still boot.

    Raises:
        ControlPortError: if the value is not an integer TCP port.

    """
    raw = os.environ.get(CONTROL_PORT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_CONTROL_PORT

    text = raw.strip()
    try:
        port = int(text)
    except ValueError as exc:
        msg = f"{CONTROL_PORT_ENV}={text!r} is not an integer port"
        raise ControlPortError(msg) from exc

    if not MIN_PORT <= port <= MAX_PORT:
        msg = f"{CONTROL_PORT_ENV}={port} is outside the range {MIN_PORT}-{MAX_PORT}"
        raise ControlPortError(msg)

    return port


def get_control_endpoint() -> tuple[str, int]:
    """Return the ``(host, port)`` the control plane binds and clients dial."""
    return (CONTROL_HOST, get_control_port())


def control_token_path() -> Path:
    """Return the path consulted for a control token file.

    Honours ``XAUUSD_CONFIG_DIR`` so a supervisor can keep credentials outside
    the repository checkout; otherwise the token lives at
    ``<repo root>/config/control.token``.
    """
    override = os.environ.get(CONFIG_DIR_ENV)
    base = Path(override).expanduser().resolve() if override else repository_root()
    return base.joinpath(*CONTROL_TOKEN_PATH)


def _read_token_file(path: Path) -> str:
    """Return the first meaningful token in ``path``, or ``""``.

    Both layouts are accepted: a bare token file, and an env-style
    ``XAUUSD_CONTROL_TOKEN=...`` file. Comments and blank lines are skipped.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        _, separator, value = text.partition("=")
        if separator:
            text = value.strip().strip("'\"")
        return text.strip()
    return ""


def get_control_token() -> str:
    """Return the shared control-plane token.

    Resolution order:

    1. ``XAUUSD_CONTROL_TOKEN`` when set to a non-blank value.
    2. The first entry of the token file at :func:`control_token_path`.

    Raises:
        ControlTokenError: if neither location yields a token.

    """
    from_env = os.environ.get(CONTROL_TOKEN_ENV)
    if from_env is not None and from_env.strip():
        return from_env.strip()

    token_file = control_token_path()
    if token_file.is_file():
        from_file = _read_token_file(token_file)
        if from_file:
            return from_file

    msg = (
        f"control token is not configured: set {CONTROL_TOKEN_ENV} "
        f"or write a token to {token_file}"
    )
    raise ControlTokenError(msg)
