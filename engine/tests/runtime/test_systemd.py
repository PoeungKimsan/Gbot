"""Tests for the ``sd_notify`` wrapper.

The wrapper has one rule worth testing: **a watchdog ping is emitted only when the
event loop, the journal thread, and the bounded queue are all healthy.** Under systemd
a ping is a claim that the service is alive, and a process that pings while its journal
is wedged is telling the supervisor to leave it to die quietly.

The rest is the fallback discipline. Without ``NOTIFY_SOCKET`` the wrapper does nothing
and says so, which is what a non-systemd host (and Windows, which has no
datagram-to-unix-socket path at all) needs. A transport that fails must not take the
engine down either: the socket disappearing when systemd restarts is a systemd problem,
not a trading problem.
"""

import datetime as dt
from collections.abc import Mapping

import pytest

from engine.runtime.systemd import (
    NullNotifyTransport,
    SdNotifySocketTransport,
    StaticHealthProbe,
    SystemdNotifier,
    is_systemd_available,
    parse_watchdog_usec,
)

NOTIFY_SOCKET = "NOTIFY_SOCKET"
WATCHDOG_USEC = "WATCHDOG_USEC"


class RecordingNotifyTransport:
    """A transport that remembers every datagram it was asked to send."""

    available: bool = True

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def send(self, message: str, payload: Mapping[str, str]) -> None:
        self.messages.append(dict(payload))


class FailingNotifyTransport:
    """A transport whose socket has gone away."""

    available: bool = True

    def send(self, message: str, payload: Mapping[str, str]) -> None:
        raise OSError("socket is gone")


class FakeClock:
    def __init__(self) -> None:
        self.now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)

    def advance(self, seconds: int) -> None:
        self.now += dt.timedelta(seconds=seconds)

    def __call__(self) -> dt.datetime:
        return self.now


class Probe:
    """A health probe the test drives by hand."""

    def __init__(self, healthy: bool = True, reason: str = "") -> None:
        self._healthy = healthy
        self.reason = reason
        self.calls = 0

    def probe(self) -> tuple[bool, str]:
        self.calls += 1
        return self._healthy, self.reason


def _notifier(
    transport: object, probe: Probe, clock: FakeClock | None = None, **kwargs: object
) -> SystemdNotifier:
    return SystemdNotifier(
        transport=transport,
        health=probe,
        clock=clock or FakeClock(),
        **kwargs,
    )


@pytest.fixture
def notify_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")
    monkeypatch.delenv(WATCHDOG_USEC, raising=False)


# --------------------------------------------------------------------------- #
# availability and the fallback
# --------------------------------------------------------------------------- #
def test_without_notify_socket_nothing_is_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTIFY_SOCKET, raising=False)
    notifier = SystemdNotifier(health=Probe())

    assert not notifier.available
    assert notifier.ready() is False
    assert notifier.status("trading") is False
    assert notifier.watchdog() is False


def test_a_blank_notify_socket_is_the_same_as_an_absent_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "   ")
    notifier = SystemdNotifier(health=Probe())

    assert not notifier.available
    assert notifier.ready() is False


def test_the_default_transport_does_nothing_without_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default is the no-op, which is what a host without systemd needs."""
    monkeypatch.delenv(NOTIFY_SOCKET, raising=False)

    notifier = SystemdNotifier(health=Probe())

    assert not notifier.available
    assert notifier.ready() is False
    assert notifier.watchdog() is False


def test_windows_falls_back_to_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")
    monkeypatch.setattr("engine.runtime.systemd.is_windows", lambda: True)

    notifier = SystemdNotifier(health=Probe())

    assert not notifier.available
    assert notifier.ready() is False
    assert notifier.watchdog() is False


def test_the_null_transport_accepts_everything() -> None:
    """The no-op transport is the shape every fallback installs."""
    null = NullNotifyTransport()

    null.send("READY=1", {})
    null.send("STATUS=x", {})
    null.send("WATCHDOG=1", {})


# --------------------------------------------------------------------------- #
# the datagrams
# --------------------------------------------------------------------------- #
def test_ready_emits_ready_1(notify_env: None) -> None:
    transport = RecordingNotifyTransport()
    notifier = _notifier(transport, Probe())

    assert notifier.ready() is True
    assert transport.messages == [{"READY": "1"}]


def test_status_emits_the_status_line(notify_env: None) -> None:
    transport = RecordingNotifyTransport()
    notifier = _notifier(transport, Probe())

    assert notifier.status("PAPER trading") is True
    assert transport.messages == [{"STATUS": "PAPER trading"}]


def test_a_status_line_replaces_its_newlines(notify_env: None) -> None:
    """A newline in a status would otherwise be smuggled through as a message."""
    transport = RecordingNotifyTransport()
    notifier = _notifier(transport, Probe())

    notifier.status("line one\nSTOPPING=1")

    assert transport.messages == [{"STATUS": "line one STOPPING=1"}]


def test_watchdog_emits_when_everything_is_healthy(notify_env: None) -> None:
    transport = RecordingNotifyTransport()
    probe = Probe()

    assert _notifier(transport, probe).watchdog() is True
    assert transport.messages == [{"WATCHDOG": "1"}]
    assert probe.calls == 1


def test_watchdog_is_silent_when_a_dependency_is_unhealthy(notify_env: None) -> None:
    transport = RecordingNotifyTransport()
    probe = Probe(healthy=False, reason="journal queue is full")

    assert _notifier(transport, probe).watchdog() is False
    assert transport.messages == []


def test_the_reason_for_silence_is_reported(notify_env: None) -> None:
    transport = RecordingNotifyTransport()
    probe = Probe(healthy=False, reason="journal thread is gone")
    notifier = _notifier(transport, probe)

    notifier.watchdog()

    assert notifier.last_decline == "journal thread is gone"
    assert probe.calls == 1


def test_a_static_probe_reports_what_it_was_told() -> None:
    assert StaticHealthProbe(True, "").probe() == (True, "")
    assert StaticHealthProbe(False, "not running").probe() == (False, "not running")


def test_a_failing_transport_never_raises(notify_env: None) -> None:
    """systemd restarting its socket is not a reason to stop trading."""
    notifier = SystemdNotifier(transport=FailingNotifyTransport(), health=Probe())

    assert notifier.ready() is False
    assert notifier.status("trading") is False
    assert notifier.watchdog() is False


# --------------------------------------------------------------------------- #
# the watchdog interval
# --------------------------------------------------------------------------- #
def test_no_interval_without_watchdog_usec(notify_env: None) -> None:
    notifier = _notifier(RecordingNotifyTransport(), Probe())

    assert notifier.watchdog_interval == dt.timedelta(0)


def test_the_interval_is_half_of_what_systemd_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")
    monkeypatch.setenv(WATCHDOG_USEC, "30000000")
    notifier = _notifier(RecordingNotifyTransport(), Probe())

    assert notifier.watchdog_interval == dt.timedelta(seconds=15)


def test_an_unparseable_interval_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")
    monkeypatch.setenv(WATCHDOG_USEC, "soon")
    notifier = _notifier(RecordingNotifyTransport(), Probe())

    assert notifier.watchdog_interval == dt.timedelta(0)


def test_a_zero_interval_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A zero would ping on every call, which is how a service gets killed for noise."""
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")
    monkeypatch.setenv(WATCHDOG_USEC, "0")
    notifier = _notifier(RecordingNotifyTransport(), Probe())

    assert notifier.watchdog_interval == dt.timedelta(0)


def test_a_watchdog_is_only_sent_once_its_interval_elapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")
    monkeypatch.setenv(WATCHDOG_USEC, "20000000")
    transport = RecordingNotifyTransport()
    clock = FakeClock()
    notifier = _notifier(transport, Probe(), clock)

    assert notifier.watchdog() is True
    # Straight away again, nothing: the interval has not elapsed.
    assert notifier.watchdog() is False
    assert transport.messages == [{"WATCHDOG": "1"}]

    clock.advance(5)
    assert notifier.watchdog() is False

    clock.advance(5)
    assert notifier.watchdog() is True
    assert len(transport.messages) == 2


def test_the_first_ping_ignores_the_interval(notify_env: None) -> None:
    """A service must say READY and ping once before cadence means anything."""
    transport = RecordingNotifyTransport()
    notifier = _notifier(transport, Probe())

    assert notifier.watchdog() is True
    assert transport.messages == [{"WATCHDOG": "1"}]


# --------------------------------------------------------------------------- #
# the interval parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20000000", dt.timedelta(seconds=10)),
        (" 20000000 ", dt.timedelta(seconds=10)),
        ("30000000", dt.timedelta(seconds=15)),
        ("20", dt.timedelta(microseconds=10)),
        ("", dt.timedelta(0)),
        ("soon", dt.timedelta(0)),
        ("-1", dt.timedelta(0)),
        ("0", dt.timedelta(0)),
        (None, dt.timedelta(0)),
    ],
)
def test_the_interval_parser(raw: str | None, expected: dt.timedelta) -> None:
    assert parse_watchdog_usec(raw) == expected


# --------------------------------------------------------------------------- #
# the socket transport
# --------------------------------------------------------------------------- #
def test_an_abstract_socket_address_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux passes an ``@``-prefixed abstract name, not a path."""
    monkeypatch.setenv(NOTIFY_SOCKET, "@xauusd-notify")

    transport = SdNotifySocketTransport()

    assert transport.address == "\0xauusd-notify"


def test_a_path_socket_address_is_used_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")

    transport = SdNotifySocketTransport()

    assert transport.address == "/run/systemd/notify"


def test_a_missing_socket_transport_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NOTIFY_SOCKET, raising=False)

    transport = SdNotifySocketTransport()

    assert not transport.available
    assert transport.address is None


_POSIX_ONLY = pytest.mark.skipif(
    __import__("sys").platform.startswith("win"), reason="Windows has no AF_UNIX datagrams"
)


@_POSIX_ONLY
def test_a_configured_socket_makes_the_transport_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "/run/systemd/notify")

    assert is_systemd_available()
    assert SdNotifySocketTransport().available


@_POSIX_ONLY
def test_availability_needs_a_non_blank_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(NOTIFY_SOCKET, "  ")

    assert is_systemd_available() is False
    assert not SdNotifySocketTransport().available
