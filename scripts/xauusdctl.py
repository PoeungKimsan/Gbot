"""The ``xauusdctl`` administration client.

One command per invocation, one JSON answer printed, one exit code. The client is
deliberately a plain socket: an operator runs it from a shell, a systemd unit, or a CI
job, and none of those should need an event loop.

The token and the port resolve exactly as the server does -- environment first, then
the file under ``config/`` -- so the only shared secret between the two is the token
itself, and neither side can silently prefer a different one.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    # Running as ``python scripts/xauusdctl.py``: put the engine on the path the same
    # way the tests do, without depending on how the process was launched.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine" / "src"))

from engine.runtime import platform

__all__ = [
    "DEFAULT_PORT",
    "EXIT_ERROR",
    "EXIT_OK",
    "build_request",
    "main",
    "resolve_endpoint",
    "resolve_token",
]

#: Re-exported so a caller reads one name for the port the engine binds by default.
DEFAULT_PORT: Final[int] = platform.DEFAULT_CONTROL_PORT

#: Exit codes. ``EXIT_OK`` echoes the engine's judgement; ``EXIT_ERROR`` is the client's.
EXIT_OK: Final[int] = 0
EXIT_ERROR: Final[int] = 1

#: How long the client waits for one answer. An operator's time is worth more than a
#: blocked terminal, and the engine answers on its own schedule.
TIMEOUT_SECONDS: Final[float] = 5.0

#: The commands the client knows about, in the order the help prints them.
_COMMANDS: Final[tuple[str, ...]] = (
    "status",
    "pause",
    "resume",
    "kill-flatten",
    "shutdown",
)

#: A command name becomes ``cmd`` by this rule, so the two never drift apart.
_ALIAS: Final[dict[str, str]] = {
    command: command.upper().replace("-", "_") for command in _COMMANDS
}


def build_request(command: str, *, token: str) -> dict[str, str]:
    """Turn a command name into the request the control plane expects.

    Args:
        command: One of ``_COMMANDS``.
        token: The shared control token.

    Returns:
        ``{"cmd": ..., "token": ...}``.

    Raises:
        ValueError: if the command is not one this client implements.
    """
    key = command.strip().lower()
    if key not in _ALIAS:
        raise ValueError(f"unknown command {command!r}; expected one of {', '.join(_COMMANDS)}")
    if not isinstance(token, str) or not token:
        raise ValueError("a control-plane token is required")
    return {"cmd": _ALIAS[key], "token": token}


def resolve_token() -> str:
    """The shared control token, from the environment or the token file.

    Raises:
        ControlTokenError: if neither location yields a token.
    """
    return platform.get_control_token()


def resolve_endpoint() -> tuple[str, int]:
    """The ``(host, port)`` the control plane is listening on."""
    return platform.get_control_endpoint()


def _send(request: dict[str, str], *, host: str, port: int) -> dict[str, object]:
    """Send one request and read one newline-delimited answer."""
    payload = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS) as handle:
        handle.sendall(payload)
        chunks: list[bytes] = []
        while True:
            chunk = handle.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if chunk.endswith(b"\n"):
                break
    text = b"".join(chunks).decode("utf-8", "replace").strip()
    if not text:
        return {"ok": False, "error": "no answer from the engine"}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"unparseable answer: {text!r}"}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "the answer was not an object"}
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xauusdctl",
        description="Administration client for the XAUUSD engine's control plane.",
    )
    parser.add_argument("command", choices=_COMMANDS, help="the command to send")
    parser.add_argument(
        "--host",
        default=None,
        help="control-plane host (defaults to 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"control-plane port (defaults to {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="control-plane token (defaults to the environment or config/control.token)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the whole answer rather than a summary",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the client.

    Returns:
        ``EXIT_OK`` when the engine answered ``ok: true``, ``EXIT_ERROR`` otherwise --
        including when the engine cannot be reached at all.
    """
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        request = build_request(args.command, token=args.token or resolve_token())
    except ValueError as exc:
        print(f"xauusdctl: {exc}", file=sys.stderr)
        return EXIT_ERROR

    host, port = resolve_endpoint()
    if args.host is not None:
        host = args.host
    if args.port is not None:
        port = args.port

    try:
        answer = _send(request, host=host, port=port)
    except OSError as exc:
        print(f"xauusdctl: cannot reach {host}:{port}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(answer, sort_keys=True, separators=(",", ":")))
    elif answer.get("ok"):
        print(_summary(args.command, answer))
    else:
        print(f"xauusdctl: {answer.get('error', 'refused')}", file=sys.stderr)

    return EXIT_OK if answer.get("ok") else EXIT_ERROR


def _summary(command: str, answer: dict[str, object]) -> str:
    """A one-line answer a human can read without parsing JSON."""
    state = answer.get("state")
    if state is not None:
        return f"{command}: {state}"
    message = answer.get("message")
    if isinstance(message, str) and message:
        return f"{command}: {message}"
    return f"{command}: ok"


if __name__ == "__main__":
    raise SystemExit(main())
