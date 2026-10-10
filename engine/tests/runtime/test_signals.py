"""Tests for the cross-platform signal router.

The router has one job: make SIGTERM and SIGINT mean the same thing on every host, by
funnelling them into a single shutdown controller. The two platform backends are
different enough that each needs its own coverage, so the backend is injectable and
both are exercised here rather than whichever the host happens to provide.

The property that matters operationally is the restore: an install replaces the
handlers that were already installed (pytest's, or the shell's), and uninstalling must
put them back. A router that leaves the process's original SIGINT handler overwritten
is a hostile library.
"""

import asyncio
import os
import signal
import threading

import pytest

from engine.runtime.signals import (
    ShutdownController,
    SignalBackend,
    SignalRouter,
    is_posix,
    is_windows,
)

#: Signals that exist everywhere, so a test never names one the host lacks.
TERM = signal.SIGTERM
INTERRUPT = signal.SIGINT

_POSIX_ONLY = pytest.mark.skipif(is_windows(), reason="POSIX signal handling is unavailable")


async def _tick() -> None:
    """Let the loop run whatever a thread-safe hop queued onto it."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# --------------------------------------------------------------------------- #
# the controller
# --------------------------------------------------------------------------- #
def test_shutdown_starts_unrequested() -> None:
    controller = ShutdownController()

    assert not controller.requested
    assert controller.reason == ""
    assert controller.requested_at is None


def test_requesting_shutdown_latches_and_records_the_reason() -> None:
    controller = ShutdownController()

    controller.request_shutdown("SIGTERM")

    assert controller.requested
    assert controller.reason == "SIGTERM"
    assert controller.requested_at is not None


def test_a_second_request_keeps_the_first_reason() -> None:
    """Whoever asked first is the answer to 'why did we stop?'."""
    controller = ShutdownController()

    controller.request_shutdown("SIGTERM")
    controller.request_shutdown("control plane")

    assert controller.reason == "SIGTERM"


def test_reset_clears_the_latch() -> None:
    controller = ShutdownController()
    controller.request_shutdown("SIGTERM")

    controller.reset()

    assert not controller.requested
    assert controller.reason == ""


def test_wait_returns_once_a_trigger_fires() -> None:
    async def scenario() -> None:
        controller = ShutdownController()

        async def trip() -> None:
            await asyncio.sleep(0)
            controller.request_shutdown("unit")

        await asyncio.gather(trip(), controller.wait())

        assert controller.requested
        assert controller.reason == "unit"

    asyncio.run(scenario())


def test_the_controller_is_safe_from_another_thread() -> None:
    """A Windows signal handler runs on the main thread outside the loop."""
    controller = ShutdownController()
    done = threading.Event()

    def trip() -> None:
        controller.request_shutdown("signal.signal")
        done.set()

    threading.Thread(target=trip, daemon=True).start()
    assert done.wait(timeout=5)
    assert controller.requested


# --------------------------------------------------------------------------- #
# platform detection
# --------------------------------------------------------------------------- #
def test_exactly_one_platform_is_reported() -> None:
    assert is_posix() is not is_windows()


def test_the_posix_backend_can_be_asked_for_directly() -> None:
    router = SignalRouter(
        controller=ShutdownController(), backend=SignalBackend.POSIX, signals=(TERM,)
    )

    assert router.backend is SignalBackend.POSIX


# --------------------------------------------------------------------------- #
# the POSIX backend
# --------------------------------------------------------------------------- #
@_POSIX_ONLY
def test_a_raised_signal_reaches_the_controller() -> None:
    """The handler the loop installs must actually latch the shutdown."""

    async def scenario() -> None:
        controller = ShutdownController()
        router = SignalRouter(controller=controller, backend=SignalBackend.POSIX, signals=(TERM,))
        router.install(asyncio.get_running_loop())
        try:
            os.kill(os.getpid(), TERM)
            await _tick()
            assert controller.requested
        finally:
            router.uninstall(asyncio.get_running_loop())

    asyncio.run(scenario())


@_POSIX_ONLY
def test_an_interrupt_reaches_the_controller_too() -> None:

    async def scenario() -> None:
        controller = ShutdownController()
        router = SignalRouter(controller=controller, backend=SignalBackend.POSIX, signals=(TERM,))
        router.install(asyncio.get_running_loop(), signals=(TERM, INTERRUPT))
        try:
            os.kill(os.getpid(), INTERRUPT)
            await _tick()
            assert controller.requested
        finally:
            router.uninstall(asyncio.get_running_loop())

    asyncio.run(scenario())


@_POSIX_ONLY
def test_uninstall_restores_the_previous_handlers() -> None:
    """Leaving the process's original handlers overwritten is not acceptable."""

    async def scenario() -> None:
        controller = ShutdownController()
        router = SignalRouter(controller=controller, backend=SignalBackend.POSIX, signals=(TERM,))
        before = signal.getsignal(TERM)
        loop = asyncio.get_running_loop()
        router.install(loop)
        router.uninstall(loop)

        assert signal.getsignal(TERM) is before

    asyncio.run(scenario())


@_POSIX_ONLY
def test_installing_twice_is_refused() -> None:

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        router = SignalRouter(
            controller=ShutdownController(), backend=SignalBackend.POSIX, signals=(TERM,)
        )
        router.install(loop)
        try:
            with pytest.raises(RuntimeError):
                router.install(loop)
        finally:
            router.uninstall(loop)

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# the Windows backend
# --------------------------------------------------------------------------- #
def test_the_windows_backend_hands_off_through_call_soon_threadsafe() -> None:
    """The handler runs off-loop, so the controller must be reached safely.

    The Windows path is exercised on every host on purpose: it is C-level signal
    handling plus a thread-safe hop onto the loop, which POSIX provides as well. The
    handler is invoked the way the C runtime invokes it -- number and frame -- rather
    than by raising a signal, because on Windows ``os.kill(SIGTERM)`` terminates the
    process outright and would never reach the handler.
    """

    async def scenario() -> None:
        controller = ShutdownController()
        router = SignalRouter(
            controller=controller, backend=SignalBackend.WINDOWS, signals=(TERM,)
        )
        loop = asyncio.get_running_loop()
        router.install(loop)
        try:
            handler = signal.getsignal(TERM)
            handler(TERM, None)
            # The handler only queues work onto the loop, so it needs a trip round it.
            await _tick()
            assert controller.requested
        finally:
            router.uninstall(loop)

    asyncio.run(scenario())


def test_the_windows_backend_restores_the_previous_handlers() -> None:

    async def scenario() -> None:
        controller = ShutdownController()
        router = SignalRouter(controller=controller, backend=SignalBackend.WINDOWS, signals=(TERM,))
        before = signal.getsignal(TERM)
        loop = asyncio.get_running_loop()
        router.install(loop)
        router.uninstall(loop)

        assert signal.getsignal(TERM) is before

    asyncio.run(scenario())
