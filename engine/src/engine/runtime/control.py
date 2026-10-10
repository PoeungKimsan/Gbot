"""The loopback control plane: the only door into a running engine.

Everything that can move money or stop the engine comes through here, so the shape is
deliberately narrow. One TCP listener on ``127.0.0.1``, one JSON object per line, one
answer per line. A client never has to guess whether a reply is a success or an error,
because every command answers exactly once.

Three rules, each of which is a way a naive version of this goes wrong:

* **The token is compared with ``hmac.compare_digest``.** ``==`` returns as soon as two
  strings differ, so a caller who can time the response learns the token a character at
  a time. Constant time is the whole point, and it is why this module does not compare
  tokens itself.
* **The peer is checked as well as the bind.** Binding ``127.0.0.1`` is the main
  defence, but a wildcard bind, a port forward, or a container network would let the
  outside world in. The listener asks every peer where it came from and hangs up on
  anything that is not this machine.
* **The surface is a protocol, not an object.** The server knows how commands are
  worded and how they are authenticated; it has no idea what the engine is doing. The
  supervisor owns that, which is what makes this testable without a running engine.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    pass

__all__ = [
    "MAX_REQUEST_BYTES",
    "ControlCommand",
    "ControlRequest",
    "ControlServer",
    "ControlSurface",
    "is_loopback_peer",
    "parse_request_line",
]

#: A request larger than this is not a request; it is an attempt to make the server
#: buffer without bound while it waits for a newline that will never arrive.
MAX_REQUEST_BYTES: int = 8192


class ControlCommand(StrEnum):
    """Every command the control plane accepts."""

    STATUS = "STATUS"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    KILL_FLATTEN = "KILL_FLATTEN"
    SHUTDOWN = "SHUTDOWN"

    @classmethod
    def parse(cls, value: object) -> ControlCommand | None:
        """Parse a command name, tolerating case and surrounding whitespace."""
        if not isinstance(value, str):
            return None
        text = value.strip().upper()
        if not text:
            return None
        try:
            return cls(text)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class ControlRequest:
    """One parsed request line.

    ``command`` is ``None`` when the line is well-formed JSON but names a command this
    server does not implement. That is a request the server can refuse with an answer,
    which is different from a line it cannot read at all.
    """

    command: ControlCommand | None
    token: str
    raw: Mapping[str, object]


class ControlSurface(Protocol):
    """What the control plane can ask of a running engine."""

    def status(self) -> Mapping[str, object]:
        """The current state, as a mapping the server merges into its answer."""

    def pause(self) -> str:
        """Stop opening new positions. Returns a human-readable outcome."""

    def resume(self) -> str:
        """Allow new positions again. Returns a human-readable outcome."""

    def kill_flatten(self) -> str:
        """Flatten the book and cancel resting orders now. Returns the outcome."""

    def request_shutdown(self) -> str:
        """Ask the engine to stop gracefully. Returns the outcome."""


def parse_request_line(line: str | bytes) -> ControlRequest | None:
    """Parse one newline-delimited request.

    Returns:
        The :class:`ControlRequest`, or ``None`` when the line is not a single JSON
        object -- which is a protocol error rather than a command error, so it gets no
        command-shaped answer.
    """
    text = line.decode("utf-8", "replace") if isinstance(line, bytes) else line
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None

    token = parsed.get("token")
    command = ControlCommand.parse(parsed.get("cmd"))
    return ControlRequest(
        command=command,
        token=token if isinstance(token, str) else "",
        raw=parsed,
    )


def is_loopback_peer(peername: object) -> bool:
    """Whether a peer address belongs to this machine.

    Loopback is 127.0.0.0/8 as well as ::1, so 127.0.0.1, 127.1.2.3 and ::1 all pass.
    An IPv4-mapped IPv6 address (``::ffff:10.0.0.7``) is refused, because it is a
    remote address wearing a loopback hat.
    """
    if not isinstance(peername, str | tuple) or isinstance(peername, bool):
        return False
    host = peername if isinstance(peername, str) else peername[0]
    if not isinstance(host, str) or not host.strip():
        return False

    candidate = host.strip()
    if candidate == "localhost":
        return True
    # An IPv4-mapped address carries the real address in its tail.
    if candidate.lower().startswith("::ffff:") or candidate.lower().startswith("::1:127."):
        candidate = candidate.rsplit(":", 1)[-1]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


class ControlServer:
    """A newline-delimited JSON control server bound to loopback.

    Args:
        surface: The engine-facing commands are dispatched to.
        token: The shared secret every request must carry.
        host: Bind address. Defaults to loopback, which is the only sanctioned value.
        port: TCP port; ``0`` picks a free one, which is what tests want.
    """

    def __init__(
        self,
        *,
        surface: ControlSurface,
        token: str,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("the control plane requires a non-empty token")
        self._surface = surface
        self._token = token
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None

    # -- lifecycle ------------------------------------------------------------ #
    async def start(self) -> None:
        """Bind and listen. Idempotent-free: a second call raises."""
        if self._server is not None:
            raise RuntimeError("control server is already started")
        self._server = await asyncio.start_server(self._serve, self._host, self._port)
        bound = self._server.sockets[0].getsockname()
        self._port = int(bound[1])

    async def stop(self) -> None:
        """Close the listener. Safe to call on a server that never started."""
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    @property
    def port(self) -> int:
        """The port actually bound, which is only known after :meth:`start`."""
        return self._port

    @property
    def host(self) -> str:
        return self._host

    @property
    def sockets(self) -> list[tuple[str, int]]:
        """The addresses actually bound, so a test can check the bind itself."""
        if self._server is None:
            return []
        return [
            (str(bound[0]), int(bound[1]))
            for bound in (sock.getsockname() for sock in self._server.sockets)
        ]

    def accepts_peer(self, peername: object) -> bool:
        """Whether a peer at this address may talk to the server at all."""
        return is_loopback_peer(peername)

    # -- the connection -------------------------------------------------------- #
    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        if not self.accepts_peer(peer):
            # No answer, not even an error: a remote caller learns nothing, not even
            # that there is a service here.
            writer.close()
            await writer.wait_closed()
            return

        try:
            while True:
                line = await self._next_line(reader)
                if line is None:
                    break
                response = self._dispatch(line)
                writer.write(self._encode(response))
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except ValueError:
            # A line longer than the cap arrives as a read timeout without a newline.
            writer.write(self._encode({"ok": False, "error": "request too long"}))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _next_line(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read one newline-terminated line, or ``None`` at end of stream."""
        try:
            line = await reader.readuntil(b"\n")
        except asyncio.LimitOverrunError as exc:
            raise ValueError(str(exc)) from exc
        except asyncio.IncompleteReadError as exc:
            # A client that hung up mid-line did not send a request.
            if not exc.partial:
                return None
            line = exc.partial
        if len(line) > MAX_REQUEST_BYTES:
            raise ValueError("request too long")
        return line

    def _dispatch(self, line: bytes) -> dict[str, object]:
        """Turn one request line into exactly one answer."""
        request = parse_request_line(line)
        if request is None:
            return {"ok": False, "error": "malformed request"}

        if not self._token_matches(request.token):
            return {"ok": False, "cmd": _cmd_name(request.command), "error": "unauthorized"}

        if request.command is None:
            return {"ok": False, "cmd": None, "error": "unknown command"}

        try:
            payload = self._run(request.command)
        except Exception as exc:
            # A command that fails must answer. Swallowing the exception silently would
            # leave a client waiting for a line that never comes.
            return {"ok": False, "cmd": request.command.value, "error": str(exc)}

        answer: dict[str, object] = {"ok": True, "cmd": request.command.value}
        answer.update(payload)
        return answer

    def _run(self, command: ControlCommand) -> dict[str, object]:
        """Dispatch one authenticated command to the surface."""
        match command:
            case ControlCommand.STATUS:
                return dict(self._surface.status())
            case ControlCommand.PAUSE:
                return {"message": self._surface.pause()}
            case ControlCommand.RESUME:
                return {"message": self._surface.resume()}
            case ControlCommand.KILL_FLATTEN:
                return {"message": self._surface.kill_flatten()}
            case ControlCommand.SHUTDOWN:
                return {"message": self._surface.request_shutdown()}

    def _token_matches(self, candidate: str) -> bool:
        """Constant-time comparison, so the token does not leak through timing."""
        return hmac.compare_digest(
            candidate.encode("utf-8", "replace"), self._token.encode("utf-8")
        )

    @staticmethod
    def _encode(payload: Mapping[str, object]) -> bytes:
        """One object, one line, sorted keys: the answer is reproducible."""
        text = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
        return (text + "\n").encode("utf-8")


def _cmd_name(command: ControlCommand | None) -> str | None:
    return None if command is None else command.value
