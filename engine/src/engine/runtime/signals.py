"""Cross-platform signal routing for the engine's shutdown latch.

A trading engine must stop the same way on every host: SIGTERM from systemd or a
console Ctrl-C, and SIGINT from a terminal, all funnel into one latch the supervisor
awaits. The two platform mechanisms are genuinely different and neither can be made to
look like the other:

* **POSIX** has `loop.add_signal_handler`, which wakes the loop itself. That is the
  right tool, because a shutdown must interrupt a loop that is blocked on a socket.
* **Windows** has no such facility. Its C-level handler runs on the main thread,
  possibly while the loop is busy, so the only safe way to reach the loop is
  `call_soon_threadsafe`.

The backend is injectable so the Windows path can be exercised on a POSIX host (and
vice versa) rather than whichever one the host happens to provide. Installing always
records the handlers it displaced and uninstalling restores them: a library that
leaves its own SIGINT handler behind breaks the process that hosts it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import signal
import sys
import threading
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "DEFAULT_SHUTDOWN_SIGNALS",
    "PLATFORM_IS_POSIX",
    "PLATFORM_IS_WINDOWS",
    "ShutdownController",
    "SignalBackend",
    "SignalRouter",
    "is_posix",
    "is_windows",
]

#: Signals every platform's supervisor traps. The CTL_CLOSE / CTL_LOGOFF events
#: Windows also sends have no portable equivalent, so the pair here is the union
#: that works everywhere rather than the union that covers everything.
DEFAULT_SHUTDOWN_SIGNALS: Final[tuple[int, ...]] = (signal.SIGTERM, signal.SIGINT)


def is_windows() -> bool:
    """Whether this host uses the Windows signal mechanism."""
    return sys.platform.startswith("win")


def is_posix() -> bool:
    """Whether this host has `loop.add_signal_handler`."""
    return not is_windows()


PLATFORM_IS_WINDOWS: Final[bool] = is_windows()
PLATFORM_IS_POSIX: Final[bool] = is_posix()


class SignalBackend(StrEnum):
    """How a signal reaches the running loop."""

    #: `loop.add_signal_handler`, which wakes the loop directly.
    POSIX = "POSIX"

    #: `signal.signal` plus `loop.call_soon_threadsafe`, for hosts without the above.
    WINDOWS = "WINDOWS"

    @classmethod
    def for_host(cls) -> SignalBackend:
        """The backend this host actually provides."""
        return cls.WINDOWS if is_windows() else cls.POSIX


class ShutdownController:
    """The single latch every shutdown trigger sets.

    Several things can ask an engine to stop: a signal, a control-plane command, a
    risk limit, or the feed giving up. They race, so the latch records the *first*
    reason and keeps it -- "why did we stop" has one answer, and it is whoever got
    there first.

    Instances are safe to touch from any thread: a Windows signal handler runs on the
    main thread while the loop is busy elsewhere, and it must not have to take a lock
    the loop holds.
    """

    __slots__ = ("_event", "_reason", "_requested_at")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = ""
        self._requested_at: dt.datetime | None = None

    @property
    def requested(self) -> bool:
        """Whether a shutdown has been asked for."""
        return self._event.is_set()

    @property
    def reason(self) -> str:
        """Why the shutdown was asked for, or an empty string before it is."""
        return self._reason

    @property
    def requested_at(self) -> dt.datetime | None:
        """When the first shutdown was asked for."""
        return self._requested_at

    def request_shutdown(self, reason: str = "") -> None:
        """Latch the shutdown, recording ``reason`` if it is the first request."""
        first = not self._event.is_set()
        if first and not self._reason:
            self._reason = reason
            self._requested_at = dt.datetime.now(dt.UTC)
        self._event.set()

    def reset(self) -> None:
        """Clear the latch so the controller can be reused.

        Deliberately idempotent and argument-free: a restart path must not be able to
        forget *why* it stopped and carry on as if nothing happened.
        """
        self._reason = ""
        self._requested_at = None
        self._event.clear()

    @property
    def event(self) -> threading.Event:
        """The underlying latch, for code that wants to wait without the loop."""
        return self._event

    async def wait(self) -> str:
        """Block until a shutdown is requested, then return the reason."""
        await asyncio.get_running_loop().run_in_executor(None, self._event.wait)
        return self._reason


class SignalRouter:
    """Installs, and can remove, the handlers that latch a shutdown.

    Args:
        controller: The latch every trapped signal reaches.
        backend: Which mechanism to use. Defaults to whatever the host provides.
        signals: The signals to trap. Defaults to SIGTERM and SIGINT.
    """

    __slots__ = ("_backend", "_controller", "_installed", "_previous", "_signals")

    def __init__(
        self,
        *,
        controller: ShutdownController,
        backend: SignalBackend | None = None,
        signals: Sequence[int] = DEFAULT_SHUTDOWN_SIGNALS,
    ) -> None:
        self._controller = controller
        self._backend = backend if backend is not None else SignalBackend.for_host()
        self._signals: tuple[int, ...] = tuple(signals)
        self._installed = False
        self._previous: dict[int, object] = {}

    @property
    def controller(self) -> ShutdownController:
        return self._controller

    @property
    def backend(self) -> SignalBackend:
        return self._backend

    @property
    def signals(self) -> tuple[int, ...]:
        return self._signals

    @property
    def installed(self) -> bool:
        return self._installed

    def install(self, loop: asyncio.AbstractEventLoop) -> None:
        """Trap every configured signal on ``loop``.

        Raises:
            RuntimeError: if the router is already installed, or the host cannot
                provide the requested backend.
        """
        if self._installed:
            raise RuntimeError("signal router is already installed")

        for number in self._signals:
            if not isinstance(number, int) or number <= 0:
                raise ValueError(f"signal numbers must be positive ints, got {number!r}")

        if self._backend is SignalBackend.POSIX:
            self._install_posix(loop)
        else:
            self._install_windows(loop)
        self._installed = True

    def uninstall(self, loop: asyncio.AbstractEventLoop) -> None:
        """Restore every handler this router displaced, and forget the signals.

        Idempotent, and safe to call from a loop that is shutting down: a restore that
        raises would leave the process in a worse state than the one being exited.
        """
        if not self._installed:
            return
        if self._backend is SignalBackend.POSIX:
            for number in self._previous:
                loop.remove_signal_handler(number)
        else:
            for number, handler in self._previous.items():
                signal.signal(number, handler)  # type: ignore[arg-type]
        self._previous.clear()
        self._installed = False

    # -- backends ------------------------------------------------------------- #
    def _install_posix(self, loop: asyncio.AbstractEventLoop) -> None:
        if not hasattr(loop, "add_signal_handler"):
            raise RuntimeError(
                "this loop has no add_signal_handler; the POSIX backend is unavailable"
            )
        for number in self._signals:
            reason = _reason_of(number)
            loop.add_signal_handler(number, self._controller.request_shutdown, reason)
            # The loop's own bookkeeping, not signal.getsignal: what has to go back is
            # what the loop had, and the loop owns it. The value is only a marker.
            self._previous[number] = loop

    def _install_windows(self, loop: asyncio.AbstractEventLoop) -> None:
        for number in self._signals:
            self._previous[number] = signal.getsignal(number)
            reason = _reason_of(number)
            # The handler runs on the main thread, possibly while the loop is mid-task,
            # so it must only hand off -- never touch loop state directly. ``reason``
            # is bound as a default argument, because the loop variable would otherwise
            # be whatever the last iteration left behind by the time a signal arrived.
            signal.signal(
                number,
                lambda _number, _frame, _reason=reason: loop.call_soon_threadsafe(
                    self._controller.request_shutdown, _reason
                ),
            )


def _reason_of(number: int) -> str:
    """The reason a trapped signal records: its portable name, or its number."""
    try:
        return signal.Signals(number).name
    except ValueError:  # pragma: no cover - every real signal has a name
        return str(number)
