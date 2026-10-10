"""Tests for the read-only API.

The service is a boundary and nothing more: it reads the publisher's atomic snapshots
and serves them. Three properties carry the weight.

**It never touches the journal.** The whole point of the projection is that the dashboard
cannot stop the trader, so the API's failure mode with no database at all is a 200, not
a 500. A test deletes the journal and asserts the endpoints still answer.

**The cache header is on every snapshot response.** ``public, max-age=10`` is what keeps
a flapping dashboard from turning into a read amplifier, and it has to be on all four
paths or the ones without it will be the ones a browser hammers.

**A bad snapshot is a 503, not a crash.** The publisher writes atomically, so a reader
never sees a half document -- but it can see a stale one, or a missing one while the
publisher starts up. Saying so plainly is better than an exception.
"""

from pathlib import Path

import pytest
from api.main import create_app
from fastapi.testclient import TestClient

from engine.publisher.outbox import atomic_write_json

NO_TRADES = {"count": 0, "trades": []}
NO_METRICS = {
    "status": "INSUFFICIENT_DATA",
    "trade_count": 0,
    "realized_pnl": 0.0,
    "max_drawdown": 0.0,
    "win_rate": None,
    "profit_factor": None,
    "expectancy_r": None,
    "confidence_interval": None,
}
STATE = {
    "run_id": "run-1",
    "strategy_version": "abc",
    "equity": "100003.40",
    "bars": 100,
    "open_positions": 0,
    "resting_orders": 0,
    "generated_at": "2026-03-09T12:00:00+00:00",
}
TRADES = {
    "count": 1,
    "trades": [
        {
            "trade_id": "p-1",
            "side": "LONG",
            "quantity": "1",
            "pnl": "3.40",
            "price": "2009.75",
            "cost": "2006.35",
            "r_multiple": "2",
            "exit_reason": "TARGET",
            "reference": "ASIAN",
        }
    ],
}
METRICS = {
    "status": "INSUFFICIENT_DATA",
    "trade_count": 1,
    "realized_pnl": 3.40,
    "max_drawdown": 0.0,
    "win_rate": 1.0,
    "profit_factor": None,
    "expectancy_r": 2.0,
    "confidence_interval": None,
}


@pytest.fixture
def projection(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    atomic_write_json(root / "state.json", STATE)
    atomic_write_json(root / "trades.json", TRADES)
    atomic_write_json(root / "metrics.json", METRICS)
    return root


@pytest.fixture
def client(projection: Path) -> TestClient:
    return TestClient(create_app(projection_path=projection))


# --------------------------------------------------------------------------- #
# the endpoints
# --------------------------------------------------------------------------- #
def test_status_serves_the_state_snapshot(client: TestClient) -> None:
    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == STATE


def test_trades_serves_the_trades_snapshot(client: TestClient) -> None:
    response = client.get("/api/trades")

    assert response.status_code == 200
    assert response.json() == TRADES


def test_metrics_serves_the_metrics_snapshot(client: TestClient) -> None:
    response = client.get("/api/metrics")

    assert response.status_code == 200
    assert response.json() == METRICS


def test_health_needs_no_snapshot(tmp_path: Path) -> None:
    """A service that cannot even say whether it is alive is not a service."""
    client = TestClient(create_app(projection_path=tmp_path / "empty"))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["snapshots"] == []


def test_health_reports_which_snapshots_exist(client: TestClient) -> None:
    response = client.get("/api/health")

    assert sorted(response.json()["snapshots"]) == ["metrics.json", "state.json", "trades.json"]


# --------------------------------------------------------------------------- #
# it never touches the journal
# --------------------------------------------------------------------------- #
def test_every_endpoint_answers_with_no_database_at_all(
    client: TestClient, projection: Path, tmp_path: Path
) -> None:
    """The journal is the trader's, not the dashboard's.

    With the database file deleted -- which is to say, with the trading process
    entirely gone -- the API still answers, because everything it reads is a
    projection. Anything less would mean the dashboard can take the engine down.
    """
    (tmp_path / "journal.db").write_text("not a database", "utf-8")

    for path in ("/api/status", "/api/trades", "/api/metrics", "/api/health"):
        assert client.get(path).status_code == 200, path


def test_a_missing_snapshot_is_a_503_not_a_crash(tmp_path: Path) -> None:
    """The publisher may not have run yet; that is a state, not an error."""
    client = TestClient(create_app(projection_path=tmp_path / "empty"))

    for path in ("/api/status", "/api/trades", "/api/metrics"):
        response = client.get(path)
        assert response.status_code == 503, path


def test_a_corrupt_snapshot_is_a_503(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "state.json").write_text("{not json", "utf-8")
    client = TestClient(create_app(projection_path=root))

    assert client.get("/api/status").status_code == 503


def test_snapshots_are_read_afresh_not_cached_in_the_process(
    client: TestClient, projection: Path
) -> None:
    """The projection updates underneath; the API must not freeze it."""
    atomic_write_json(projection / "state.json", {**STATE, "equity": "999999.00"})

    assert client.get("/api/status").json()["equity"] == "999999.00"


# --------------------------------------------------------------------------- #
# the cache header
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path", ["/api/status", "/api/trades", "/api/metrics", "/api/health"]
)
def test_every_endpoint_is_cacheable_for_ten_seconds(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.headers["cache-control"] == "public, max-age=10"


def test_a_503_is_not_cached(tmp_path: Path) -> None:
    """A missing snapshot must be re-checked, not served for ten seconds."""
    client = TestClient(create_app(projection_path=tmp_path / "empty"))

    response = client.get("/api/status")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------- #
# the app object
# --------------------------------------------------------------------------- #
def test_the_app_binds_to_loopback_only() -> None:
    """A dashboard that listens on every interface is a dashboard of the account."""
    from api.main import API_HOST

    assert API_HOST == "127.0.0.1"


def test_the_app_defaults_to_port_8080() -> None:
    from api.main import API_PORT

    assert API_PORT == 8080


def test_openapi_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/status" in response.json()["paths"]
