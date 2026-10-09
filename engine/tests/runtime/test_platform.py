"""Tests for :mod:`engine.runtime.platform` (single portable control-plane path)."""

import ast
import tempfile
from pathlib import Path
from typing import Final

import pytest

from engine.runtime.platform import (
    CONTROL_TOKEN_FILENAME,
    DEFAULT_CONTROL_PORT,
    ControlPlaneError,
    ControlPortError,
    ControlTokenError,
    control_token_path,
    get_control_endpoint,
    get_control_port,
    get_control_token,
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_MODULE_PATH: Final[Path] = REPO_ROOT / "engine" / "src" / "engine" / "runtime" / "platform.py"

#: Constructs that would reintroduce per-host code paths.
_PLATFORM_BRANCHING_TOKENS: Final[tuple[str, ...]] = (
    "sys.platform",
    "os.name",
    "platform.system",
    "win32",
    "darwin",
    "linux",
)

#: Environment variables that must be cleared for a clean baseline.
_CONTROL_ENV: Final[tuple[str, ...]] = (
    "XAUUSD_CONTROL_PORT",
    "XAUUSD_CONTROL_TOKEN",
)


@pytest.fixture(autouse=True)
def _clean_control_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the developer's real environment."""
    for name in _CONTROL_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("XAUUSD_CONFIG_DIR", raising=False)


def _write_token_file(base: Path, content: str) -> Path:
    """Write ``content`` to ``<base>/config/control.token`` and return the path."""
    config_dir = base / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    token_file = config_dir / CONTROL_TOKEN_FILENAME
    token_file.write_text(content, encoding="utf-8")
    return token_file


# --------------------------------------------------------------------------- #
# endpoint
# --------------------------------------------------------------------------- #
def test_control_endpoint_defaults_to_loopback() -> None:
    host, port = get_control_endpoint()
    assert host == "127.0.0.1"
    assert port == DEFAULT_CONTROL_PORT


@pytest.mark.parametrize("port", [1, 8765, 65535])
def test_control_port_accepts_valid_values(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    monkeypatch.setenv("XAUUSD_CONTROL_PORT", str(port))
    assert get_control_port() == port


@pytest.mark.parametrize("port", ["0", "-1", "65536", "99999", "99999999999999999999"])
def test_control_port_rejects_out_of_range_values(
    monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    monkeypatch.setenv("XAUUSD_CONTROL_PORT", port)
    with pytest.raises(ControlPortError):
        get_control_port()


@pytest.mark.parametrize("value", ["abc", "8765.0", "0x10", "null", "80,80"])
def test_control_port_rejects_non_integer_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("XAUUSD_CONTROL_PORT", value)
    with pytest.raises(ControlPortError):
        get_control_port()


@pytest.mark.parametrize("value", ["8765", " 8765 ", "+8765", "8_765"])
def test_control_port_uses_int_parsing_semantics(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Parsing is intentionally ``int()``-compatible, including underscores."""
    monkeypatch.setenv("XAUUSD_CONTROL_PORT", value)
    assert get_control_port() == 8765


def test_blank_control_port_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAUUSD_CONTROL_PORT", "   ")
    assert get_control_port() == DEFAULT_CONTROL_PORT


def test_control_plane_errors_share_a_base_class() -> None:
    assert issubclass(ControlPortError, ControlPlaneError)
    assert issubclass(ControlTokenError, ControlPlaneError)


# --------------------------------------------------------------------------- #
# token
# --------------------------------------------------------------------------- #
def test_token_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", "s3cret")
    assert get_control_token() == "s3cret"


def test_token_comes_from_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path))
    _write_token_file(tmp_path, "file-token\n")

    assert get_control_token() == "file-token"
    resolved = control_token_path()
    assert resolved.name == CONTROL_TOKEN_FILENAME
    assert resolved.is_relative_to(tmp_path)


def test_environment_takes_precedence_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path))
    _write_token_file(tmp_path, "file-token\n")
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", "env-token")

    assert get_control_token() == "env-token"


def test_blank_environment_token_falls_through_to_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path))
    _write_token_file(tmp_path, "file-token\n")
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", "   \n")

    assert get_control_token() == "file-token"


def test_token_file_accepts_env_style_and_comments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path))
    _write_token_file(tmp_path, "# managed by the supervisor\n\nXAUUSD_CONTROL_TOKEN=\"quoted\"\n")

    assert get_control_token() == "quoted"


def test_comment_only_token_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path))
    _write_token_file(tmp_path, "# nothing here\n\n")

    with pytest.raises(ControlTokenError):
        get_control_token()


def test_missing_token_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path / "does-not-exist"))

    with pytest.raises(ControlTokenError):
        get_control_token()


def test_default_token_path_lives_under_config_dir_in_the_repo() -> None:
    resolved = control_token_path()

    assert resolved.name == CONTROL_TOKEN_FILENAME
    assert resolved.parent.name == "config"
    assert resolved.parent.parent == REPO_ROOT


# --------------------------------------------------------------------------- #
# portability
# --------------------------------------------------------------------------- #
def test_module_uses_one_code_path_for_every_host() -> None:
    """AGENTS.md section 2.4: a single portable path, no per-host branching."""
    source = _MODULE_PATH.read_text(encoding="utf-8")
    for token in _PLATFORM_BRANCHING_TOKENS:
        assert token not in source, f"{token!r} reintroduces host-specific branching"


def test_module_contains_no_unix_socket_usage() -> None:
    """AF_UNIX is banned repository-wide; loopback TCP is the control plane.

    AST-based so that the module docstring can explain *why* AF_UNIX is banned
    without tripping the guard.
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"), filename=str(_MODULE_PATH))

    for node in ast.walk(tree):
        is_name = isinstance(node, ast.Name) and node.id == "AF_UNIX"
        is_attr = isinstance(node, ast.Attribute) and node.attr == "AF_UNIX"
        assert not (is_name or is_attr), f"AF_UNIX referenced at line {node.lineno}"


@pytest.mark.posix_only
def test_control_plane_is_immune_to_unix_socket_length_limits() -> None:
    """AGENTS.md section 2.4: TCP avoids the 104-byte ``sun_path`` cap entirely.

    The assertion documents the constraint a future AF_UNIX reintroduction would
    have to live inside, so the trade-off stays visible in the test suite.
    """
    candidate = Path(tempfile.gettempdir()) / "xauusd.sock"

    assert len(str(candidate).encode("utf-8")) < 104, candidate
    assert get_control_endpoint()[0] == "127.0.0.1"
