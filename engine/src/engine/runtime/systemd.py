"""``sd_notify`` for a service that knows when it is actually healthy.

systemd supervises with the watchdog, so the wrapper deliberately does not answer
"am I alive" with "I am still running". A process whose journal worker has died can
loop forever, and pinging would tell systemd to leave it alone indefinitely. A health
probe -- the event loop turning over, the journal thread alive, the bounded queue not
full -- is what answers, and the wrapper only sends ``WATCHDOG=1`` when every part of
it passes.

The fallback matters as much. Without ``NOTIFY_SOCKET`` there is nothing to talk to, so
the wrapper does nothing and reports that it did; Windows has no
datagram-to-unix-socket path at all, so it gets the same no-op by construction. Neither
is an error, because a host without systemd is a perfectly good host.
"""

from __future__ import annotations

import datetime as dt
import os
import socket
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, Protocol

from engine.runtime.signals import is_windows

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "NOTIFY_SOCKET_ENV",
    "WATCHDOG_USEC_ENV",
    "HealthProbe",
    "NotifyTransport",
    "NullNotifyTransport",
    "SdNotifySocketTransport",
    "StaticHealthProbe",
    "SystemdNotifier",
    "is_systemd_available",
    "parse_watchdog_usec",
]

#: The environment variables systemd sets for a notification-capable service.
NOTIFY_SOCKET_ENV: Final[str] = "NOTIFY_SOCKET"
WATCHDOG_USEC_ENV: Final[str] = "WATCHDOG_USEC"

#: How much of the watchdog period is spent before pinging again. systemd kills a
#: service that misses two, so half leaves a full period of slack. The split is done
#: in whole microseconds by :func:`parse_watchdog_usec`, because a ``timedelta``
#: cannot be multiplied by another one.
ZERO: Final[dt.timedelta] = dt.timedelta(0)


class NotifyTransport(Protocol):
    """Where a notification datagram goes, and whether there is anywhere to go.

    ``available`` is asked before anything is sent, so a transport that is the wrong
    one for its host reports itself unavailable rather than failing at send time. An
    injected transport is trusted: a caller that hands one over has already decided
    where the datagrams go.
    """

    available: bool

    def send(self, message: str, payload: Mapping[str, str]) -> None:
        """Deliver one datagram, or raise if the socket has gone."""


class HealthProbe(Protocol):
    """Whether every dependency the engine cannot run without is healthy."""

    def probe(self) -> tuple[bool, str]:
        """Return ``(healthy, reason)``. The reason is for the log, not the wire."""


class NullNotifyTransport:
    """A transport that discards everything, for hosts with nothing to notify."""

    available: bool = False

    def send(self, message: str, payload: Mapping[str, str]) -> None:
        return None


class StaticHealthProbe:
    """A probe that always answers the same thing, for tests and warm-up."""

    __slots__ = ("_healthy", "_reason")

    def __init__(self, healthy: bool, reason: str = "") -> None:
        self._healthy = healthy
        self._reason = reason

    def probe(self) -> tuple[bool, str]:
        return self._healthy, self._reason


class SdNotifySocketTransport:
    """A datagram transport aimed at the socket named by ``NOTIFY_SOCKET``.

    Linux hands systemd's notification socket as an abstract namespace name prefixed
    with ``@``, which becomes a leading NUL byte once the NUL is stripped. The address
    is therefore not always a path, and treating it as one would silently send the
    datagram nowhere.
    """

    __slots__ = ("_socket_path",)

    def __init__(self, socket_path: str | None = None) -> None:
        source = socket_path if socket_path is not None else os.environ.get(NOTIFY_SOCKET_ENV)
        self._socket_path = source.strip() if isinstance(source, str) else ""

    @property
    def available(self) -> bool:
        """Whether there is a socket to talk to on this host."""
        if is_windows():
            return False
        return bool(self._socket_path)

    @property
    def address(self) -> str | None:
        """The datagram address, in the form ``socket.sendto`` wants."""
        if not self._socket_path:
            return None
        if self._socket_path.startswith("@"):
            return "\0" + self._socket_path[1:]
        return self._socket_path

    def send(self, message: str, payload: Mapping[str, str]) -> None:
        address = self.address
        if address is None:
            raise OSError("no NOTIFY_SOCKET configured")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as handle:
            handle.sendto(message.encode("utf-8"), address)


def is_systemd_available(socket_path: str | None = None) -> bool:
    """Whether this process runs under a supervisor that accepts notifications.

    Args:
        socket_path: Override for the environment, so a caller can ask about a
            specific socket without disturbing the process's own.

    Returns:
        ``True`` when a notification socket is configured and this host has the
        datagram path to reach it.
    """
    return SdNotifySocketTransport(socket_path).available


def parse_watchdog_usec(raw: str | None) -> dt.timedelta:
    """The ping interval implied by a ``WATCHDOG_USEC`` value.

    Half of what systemd asks for, because a service is killed after it misses two
    deadlines and half leaves a whole period of slack. Anything unparseable, zero, or
    negative yields a zero interval, which the notifier reads as "never ping".
    """
    if raw is None:
        return ZERO
    try:
        micros = int(str(raw).strip())
    except (TypeError, ValueError):
        return ZERO
    if micros <= 0:
        return ZERO
    return dt.timedelta(microseconds=micros // 2)


class SystemdNotifier:
    """Emits ``sd_notify`` datagrams, and refuses to when it should not.

    Args:
        transport: Where the datagrams go. Defaults to the configured socket, and to
            a no-op when none is configured.
        health: The probe every watchdog ping consults first.
        socket_path: Override for ``NOTIFY_SOCKET``.
        clock: Wall clock, injectable so a test can advance it deterministically.
    """

    __slots__ = ("_clock", "_health", "_interval", "_last_decline", "_last_ping", "_transport")

    def __init__(
        self,
        *,
        transport: NotifyTransport | None = None,
        health: HealthProbe | None = None,
        socket_path: str | None = None,
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._transport: NotifyTransport = (
            transport if transport is not None else SdNotifySocketTransport(socket_path)
        )
        self._health: HealthProbe = health if health is not None else StaticHealthProbe(True)
        self._interval = parse_watchdog_usec(os.environ.get(WATCHDOG_USEC_ENV))
        self._clock: Callable[[], dt.datetime] = clock if clock is not None else _utc_now
        self._last_ping: dt.datetime | None = None
        self._last_decline = ""

    # -- introspection ------------------------------------------------------- #
    @property
    def available(self) -> bool:
        """Whether there is anywhere to send a notification."""
        return self._transport.available

    @property
    def watchdog_interval(self) -> dt.timedelta:
        """How often this wrapper is willing to ping."""
        return self._interval

    @property
    def last_decline(self) -> str:
        """Why the last watchdog ping was withheld, for the log."""
        return self._last_decline

    # -- notifications -------------------------------------------------------- #
    def ready(self) -> bool:
        """Announce that startup finished. Returns whether it was sent."""
        return self._send({"READY": "1"})

    def status(self, text: str) -> bool:
        """Set the human-readable status line. Returns whether it was sent."""
        clean = " ".join(str(text).splitlines()) or "running"
        return self._send({"STATUS": clean})

    def watchdog(self) -> bool:
        """Ping the watchdog, but only when everything is healthy.

        Returns:
            ``True`` when the datagram was sent. ``False`` means it was not, and
            :attr:`last_decline` says why.
        """
        healthy, reason = self._health.probe()
        if not healthy:
            self._last_decline = reason or "a dependency is unhealthy"
            return False

        now = self._clock()
        too_soon = (
            self._interval > ZERO
            and self._last_ping is not None
            and now - self._last_ping < self._interval
        )
        if too_soon:
            return False
        self._last_ping = now
        self._last_decline = ""
        return self._send({"WATCHDOG": "1"})

    # -- internals ------------------------------------------------------------ #
    def _send(self, fields: Mapping[str, str]) -> bool:
        if not self.available:
            self._last_decline = "no notification socket"
            return False
        message = "\n".join(f"{key}={value}" for key, value in fields.items())
        try:
            self._transport.send(message, fields)
        except (OSError, ValueError):
            # A supervisor that went away is not a reason to stop trading. The next
            # ping or status call will try again and fail again, loudly, in the log.
            self._last_decline = "the notification transport failed"
            return False
        return True


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
