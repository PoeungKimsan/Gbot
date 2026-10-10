"""The read-only HTTP API: the publisher's snapshots, over HTTP.

This service is deliberately the thinnest layer in the engine. It reads three JSON
documents from a directory and serves them, and it has no other dependency -- no
database, no network, no state. That is what makes the dashboard safe: it cannot stop
the trading process, because it never opens the trading process's files.

Two boundary decisions are worth naming:

* **``Cache-Control: public, max-age=10``** on every snapshot response. The publisher
  rewrites the snapshots at its own pace, so ten seconds of caching costs nothing and
  keeps a flapping browser tab from becoming a read amplifier.
* **A missing snapshot is a 503, not a 500.** The publisher may not have run yet; that
  is a state the client can act on, not a defect in the server. It is served
  ``no-store`` so the next request re-checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from engine.publisher.outbox import SNAPSHOT_FILES, read_snapshot

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "API_HOST",
    "API_PORT",
    "CACHE_CONTROL",
    "SNAPSHOT_ROUTES",
    "create_app",
]

#: Loopback only. The engine's dashboard is not a public service.
API_HOST: Final[str] = "127.0.0.1"

#: The documented port.
API_PORT: Final[int] = 8080

#: Snapshot responses are cacheable; the publisher is the source of truth.
CACHE_CONTROL: Final[str] = "public, max-age=10"

#: Where the projection lives by default, relative to the repository root.
DEFAULT_PROJECTION_PATH: Final[Path] = Path("projection")

#: URL path for each snapshot document.
SNAPSHOT_ROUTES: Final[Mapping[str, str]] = {
    "status": "state.json",
    "trades": "trades.json",
    "metrics": "metrics.json",
}

#: Response headers for a snapshot that does not exist yet.
_NO_STORE: Final[dict[str, str]] = {"cache-control": "no-store"}


def create_app(projection_path: Path | str = DEFAULT_PROJECTION_PATH) -> FastAPI:
    """Build the API application.

    Args:
        projection_path: The directory the publisher writes its snapshots into.

    Returns:
        A FastAPI application that reads that directory and nothing else.
    """
    app = FastAPI(
        title="XAUUSD Engine",
        description="Read-only projection of the trading journal.",
        version="0.5.0",
    )
    root = Path(projection_path)

    @app.get("/api/status")
    async def status() -> JSONResponse:
        """The run header, equity mark, and counts."""
        return _serve(root / "state.json")

    @app.get("/api/trades")
    async def trades() -> JSONResponse:
        """Every closed trade, in the reporting layer's order."""
        return _serve(root / "trades.json")

    @app.get("/api/metrics")
    async def metrics() -> JSONResponse:
        """The performance report for those trades."""
        return _serve(root / "metrics.json")

    @app.get("/api/health")
    async def health() -> JSONResponse:
        """Liveness, and which snapshots are present.

        Deliberately not a snapshot: a service that cannot report its own liveness
        without the publisher is a service whose health depends on the thing it is
        reporting on. It is cacheable like the rest, because it is just as cheap.
        """
        present = sorted(name for name in SNAPSHOT_FILES if (root / name).is_file())
        return JSONResponse(
            content={"status": "ok", "snapshots": present},
            headers={"cache-control": CACHE_CONTROL},
        )

    return app


def _serve(path: Path) -> JSONResponse:
    """Read one snapshot and answer with it, or 503 if it is not there yet."""
    payload = read_snapshot(path)
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail=f"{path.name} is not available yet",
            headers=_NO_STORE,
        )
    return JSONResponse(content=payload, headers={"cache-control": CACHE_CONTROL})
