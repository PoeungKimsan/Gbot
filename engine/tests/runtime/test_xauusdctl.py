"""Tests for the ``xauusdctl`` administration client.

The client is the other side of the loopback control plane, so its contract is the
mirror of the server's: one JSON object per command, the token resolved the same way
(environment first, then the token file), and a non-zero exit when the answer is
``ok: false``.

The tests drive ``main(argv)`` directly rather than as a subprocess. That is enough
coverage for the parsing and the exit codes, and it keeps the suite fast; one test
runs the real script through a subprocess so the ``__main__`` guard is proven too.
"""

import importlib.util
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "xauusdctl.py"


def _load_client() -> object:
    """Import ``scripts/xauusdctl.py``, which is a program rather than a package."""
    spec = importlib.util.spec_from_file_location("xauusdctl", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client = _load_client()
build_request = client.build_request
main = client.main
resolve_token = client.resolve_token
resolve_endpoint = client.resolve_endpoint
DEFAULT_PORT = client.DEFAULT_PORT
EXIT_OK = client.EXIT_OK
EXIT_ERROR = client.EXIT_ERROR

#: Every command, and nothing else, is what the client can send.
COMMANDS = ("status", "pause", "resume", "kill-flatten", "shutdown")


def test_every_documented_command_is_supported() -> None:
    for command in COMMANDS:
        assert build_request(command, token="t")["cmd"] == command.upper().replace("-", "_")


def test_build_request_carries_the_token() -> None:
    request = build_request("status", token="secret")

    assert request == {"cmd": "STATUS", "token": "secret"}


def test_an_unknown_command_is_refused_before_it_reaches_the_socket() -> None:
    with pytest.raises(ValueError):
        build_request("launch-missile", token="t")


def test_the_default_port_is_the_documented_one() -> None:
    assert DEFAULT_PORT == 8765


def test_the_port_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAUUSD_CONTROL_PORT", "9001")

    host, port = resolve_endpoint()

    assert port == 9001
    assert host == "127.0.0.1"


def test_the_token_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", "from-env")

    assert resolve_token() == "from-env"


def test_the_token_falls_back_to_the_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The token file lives under the configured config directory, as Phase 1 documented."""
    monkeypatch.delenv("XAUUSD_CONTROL_TOKEN", raising=False)
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path))
    token_dir = tmp_path / "config"
    token_dir.mkdir()
    (token_dir / "control.token").write_text("from-file\n", "utf-8")

    assert resolve_token() == "from-file"


def test_a_missing_token_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No token is a refusal, not a default: the control plane has no insecure mode."""
    from engine.runtime.platform import ControlTokenError

    monkeypatch.delenv("XAUUSD_CONTROL_TOKEN", raising=False)
    monkeypatch.setenv("XAUUSD_CONFIG_DIR", str(tmp_path))

    with pytest.raises(ControlTokenError):
        resolve_token()


# --------------------------------------------------------------------------- #
# talking to a live server
# --------------------------------------------------------------------------- #
class HealthySurface:
    """The engine as the control plane sees it: every command has an outcome."""

    def status(self) -> dict[str, object]:
        return {"state": "RUNNING", "bars": 3}

    def pause(self) -> str:
        return "paused"

    def resume(self) -> str:
        return "resumed"

    def kill_flatten(self) -> str:
        return "flattened"

    def request_shutdown(self) -> str:
        return "shutting down"


def _fail(surface: object) -> None:
    for name in ("pause", "resume", "kill_flatten", "request_shutdown"):
        setattr(surface, name, lambda: "")


def _drive(surface: object, argv: list[str], *, token: str = "ctl-token") -> int:
    """Run the client against a real control server on a free port.

    The client is a blocking socket call, so it runs in a thread: the control server
    lives on this same event loop, and a client that blocked it could never be
    answered -- which is how a real pair of processes behaves as well.
    """
    import asyncio

    from engine.runtime.control import ControlServer

    async def scenario() -> int:
        server = ControlServer(surface=surface, token=token, port=0)
        await server.start()
        try:
            return await asyncio.to_thread(
                main, [*argv, "--port", str(server.port), "--token", token]
            )
        finally:
            await server.stop()

    return asyncio.run(scenario())


def test_a_wrong_token_exits_non_zero() -> None:
    """The server refuses a token it did not issue, and the client says so."""
    import asyncio

    from engine.runtime.control import ControlServer

    async def scenario() -> int:
        server = ControlServer(surface=HealthySurface(), token="ctl-token", port=0)
        await server.start()
        try:
            return await asyncio.to_thread(
                main, ["status", "--port", str(server.port), "--token", "wrong"]
            )
        finally:
            await server.stop()

    assert asyncio.run(scenario()) == EXIT_ERROR


def test_a_command_round_trips_over_a_real_socket() -> None:
    """The client drives the Phase 5 control server, not a stand-in."""
    code = _drive(HealthySurface(), ["status"])

    assert code == EXIT_OK


def test_a_refused_answer_exits_non_zero() -> None:
    class Failing(HealthySurface):
        def status(self) -> dict[str, object]:
            raise RuntimeError("the engine is not built")

    surface = Failing()
    _fail(surface)

    assert _drive(surface, ["status"]) == EXIT_ERROR



def test_no_server_is_a_clean_error_not_a_traceback() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]

    assert main(["status", "--port", str(free), "--token", "t"]) == EXIT_ERROR


def test_the_script_is_runnable_as_a_program() -> None:
    """The ``__main__`` guard is the part a direct import never exercises."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == EXIT_OK
    assert b"status" in result.stdout
