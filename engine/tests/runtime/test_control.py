"""Tests for the loopback control plane.

The control plane carries commands that make money move: flatten the book, stop the
engine. Three properties carry the weight.

**Authentication is constant-time.** ``hmac.compare_digest``, not ``==``: a comparison
that returns early on the first differing byte leaks the token one character at a time
to a caller who can measure the answer.

**Non-loopback peers never get an answer.** The server binds ``127.0.0.1``, but a
host that binds a wildcard address -- or a container that forwards a port -- would let
the outside world reach these commands. The peer is checked as well as the bind.

**One request, one answer.** Every command answers exactly one newline-delimited JSON
object, whether it succeeded or not, so a client never has to guess whether a line is
a response or an error.
"""

import asyncio
import json

import pytest

from engine.runtime.control import (
    ControlCommand,
    ControlServer,
    ControlSurface,
    is_loopback_peer,
    parse_request_line,
)

#: A token fixture is the point of these tests, not a credential.
TOKEN = "correct-horse-battery-staple"  # noqa: S105


class RecordingSurface:
    """A control surface that remembers what it was asked to do."""

    def __init__(self) -> None:
        self.paused = False
        self.killed = False
        self.shutdown = ""
        self.status_calls = 0

    def status(self) -> dict[str, object]:
        self.status_calls += 1
        return {
            "state": "PAUSED" if self.paused else "RUNNING",
            "bars": 12,
            "open_positions": 1,
            "resting_orders": 0,
        }

    def pause(self) -> str:
        self.paused = True
        return "paused at the next closed bar"

    def resume(self) -> str:
        self.paused = False
        return "resumed"

    def kill_flatten(self) -> str:
        self.killed = True
        return "flattened"

    def request_shutdown(self) -> str:
        self.shutdown = "requested"
        return "shutting down"


async def _serve(surface: ControlSurface, **kwargs: object) -> tuple[ControlServer, int]:
    server = ControlServer(surface=surface, token=TOKEN, host="127.0.0.1", port=0, **kwargs)
    await server.start()
    return server, server.port


async def _exchange(
    port: int, lines: list[dict[str, object] | str]
) -> list[dict[str, object]]:
    """Send ``lines`` and collect one answer per line."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        payload = "".join(
            f"{line if isinstance(line, str) else json.dumps(line)}\n" for line in lines
        )
        writer.write(payload.encode("utf-8"))
        await writer.drain()
        answers: list[dict[str, object]] = []
        for _ in lines:
            raw = await reader.readline()
            answers.append(json.loads(raw.decode("utf-8")))
        return answers
    finally:
        writer.close()
        await writer.wait_closed()


# --------------------------------------------------------------------------- #
# the wire format
# --------------------------------------------------------------------------- #
def test_a_status_request_is_one_json_object() -> None:
    request = parse_request_line('{"cmd":"STATUS","token":"x"}')

    assert request is not None
    assert request.command is ControlCommand.STATUS
    assert request.token == "x"  # noqa: S105


def test_an_unknown_command_is_a_request_with_no_command() -> None:
    """A command we do not implement is a request we can refuse politely."""
    request = parse_request_line('{"cmd":"FLY_ME_TO_THE_MOON","token":"x"}')

    assert request is not None
    assert request.command is None


@pytest.mark.parametrize("line", ["", "   ", "not json", "[1,2,3]", '"a string"', "42"])
def test_a_line_we_cannot_read_is_no_request(line: str) -> None:
    assert parse_request_line(line) is None


def test_an_object_with_no_command_is_a_readable_request() -> None:
    """An empty object parses; refusing it is the server's job, not the parser's."""
    request = parse_request_line("{}")

    assert request is not None
    assert request.command is None
    assert request.token == ""


def test_a_command_is_case_insensitive() -> None:
    request = parse_request_line('{"cmd":"kill_flatten","token":"x"}')

    assert request is not None
    assert request.command is ControlCommand.KILL_FLATTEN


def test_trailing_whitespace_and_a_newline_are_tolerated() -> None:
    request = parse_request_line('  {"cmd":"STATUS","token":"x"}  \n')

    assert request is not None
    assert request.command is ControlCommand.STATUS


# --------------------------------------------------------------------------- #
# authentication
# --------------------------------------------------------------------------- #
async def test_a_correct_token_gets_an_answer() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "STATUS", "token": TOKEN}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is True
    assert answers[0]["cmd"] == "STATUS"
    assert surface.status_calls == 1


async def test_a_wrong_token_is_refused() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "STATUS", "token": "nope"}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is False
    assert answers[0]["error"] == "unauthorized"
    assert surface.status_calls == 0


async def test_an_empty_token_is_refused() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "STATUS", "token": ""}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is False


async def test_a_missing_token_is_refused() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "STATUS"}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is False


async def test_one_connection_may_issue_several_commands() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(
            port,
            [
                {"cmd": "PAUSE", "token": TOKEN},
                {"cmd": "STATUS", "token": TOKEN},
                {"cmd": "RESUME", "token": TOKEN},
                {"cmd": "STATUS", "token": TOKEN},
            ],
        )
    finally:
        await server.stop()

    assert [answer["ok"] for answer in answers] == [True, True, True, True]
    assert answers[1]["state"] == "PAUSED"
    assert answers[3]["state"] == "RUNNING"
    assert surface.paused is False


async def test_a_bad_line_in_the_middle_of_a_session_does_not_kill_it() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(b"garbage\n")
            writer.write(json.dumps({"cmd": "STATUS", "token": TOKEN}).encode() + b"\n")
            await writer.drain()
            first = json.loads(await reader.readline())
            second = json.loads(await reader.readline())
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()

    assert first["ok"] is False
    assert second["ok"] is True


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
async def test_pause_stops_new_entries() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "PAUSE", "token": TOKEN}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is True
    assert surface.paused is True


async def test_resume_lets_them_back_in() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(
            port, [{"cmd": "PAUSE", "token": TOKEN}, {"cmd": "RESUME", "token": TOKEN}]
        )
    finally:
        await server.stop()

    assert answers[1]["ok"] is True
    assert surface.paused is False


async def test_kill_flatten_flattens_the_book() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "KILL_FLATTEN", "token": TOKEN}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is True
    assert surface.killed is True


async def test_shutdown_asks_the_supervisor_to_stop() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "SHUTDOWN", "token": TOKEN}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is True
    assert surface.shutdown == "requested"


async def test_an_unknown_command_is_refused_not_ignored() -> None:
    surface = RecordingSurface()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "FLY", "token": TOKEN}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is False
    assert "error" in answers[0]


async def test_a_failing_command_is_reported_not_swallowed() -> None:
    class Exploding(RecordingSurface):
        def pause(self) -> str:
            raise RuntimeError("no book to pause")

    surface = Exploding()
    server, port = await _serve(surface)
    try:
        answers = await _exchange(port, [{"cmd": "PAUSE", "token": TOKEN}])
    finally:
        await server.stop()

    assert answers[0]["ok"] is False
    assert "no book to pause" in str(answers[0]["error"])


# --------------------------------------------------------------------------- #
# loopback enforcement
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        (("127.0.0.1", 51000), True),
        (("127.0.0.1", 0), True),
        (("127.1.2.3", 51000), True),
        (("::1", 51000, 0, 0), True),
        ("localhost", True),
        (("10.0.0.7", 51000), False),
        (("192.168.1.5", 51000), False),
        (("::ffff:10.0.0.7", 51000, 0, 0), False),
        (("8.8.8.8", 53), False),
        (None, False),
    ],
)
def test_loopback_peers(peer: object, expected: bool) -> None:
    assert is_loopback_peer(peer) is expected


async def test_the_bind_itself_is_loopback() -> None:
    """The bind is the first defence, so it is asserted rather than assumed."""
    surface = RecordingSurface()
    server = ControlServer(surface=surface, token=TOKEN, host="127.0.0.1", port=0)
    await server.start()
    try:
        assert server.host == "127.0.0.1"
        assert server.sockets == [("127.0.0.1", server.port)]
        assert server.accepts_peer(("127.0.0.1", 40000))
        assert not server.accepts_peer(("10.0.0.7", 40000))
    finally:
        await server.stop()
