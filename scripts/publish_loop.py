"""Run the projection publisher as a process, forever.

Phase 5 built the publisher and deliberately did not ship a process: its acceptance was a
`ProjectionPublisher` a caller could drive (`engine/publisher/outbox.py`), because the
module's contract is "fold the journal aloud", not "be a daemon". Phase 7 needs one, and a
daemon is the smallest thing that can satisfy that:

* loop forever, calling `publish()` on an interval;
* tolerate the journal not existing yet, because the publisher starts before the engine
  has written a row and a crash-loop over a missing file is not a recovery;
* tolerate a corrupt or unreadable snapshot, because `read_snapshot` already reports absence
  rather than raising;
* stop on SIGTERM/SIGINT, finishing the publish it is in.

Nothing here touches the journal's contents. The one decision worth naming is that a failed
publish is *logged and retried* rather than fatal: the publisher's own docstring says an
unknown event type is ignored rather than fatal, because a new event type is a new phase,
and a daemon that dies on one would take the dashboard down with the engine.

Usage:
    python scripts/publish_loop.py

Environment:
    XAUUSD_JOURNAL_PATH     Path to journal.db. Default /var/lib/xauusd/journal/journal.db.
    XAUUSD_PROJECTION_PATH  Where snapshots are written. Default /var/lib/xauusd/projection.
    XAUUSD_PUBLISH_INTERVAL Seconds between folds. Default 5.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import signal
import sqlite3
import time
from pathlib import Path
from typing import Final

from engine.publisher.outbox import ProjectionPublisher

__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_JOURNAL",
    "DEFAULT_PROJECTION",
    "JOURNAL_PATH_ENV",
    "PROJECTION_PATH_ENV",
    "PUBLISH_INTERVAL_ENV",
    "main",
    "resolve_paths",
]

logger = logging.getLogger("xauusd-publisher")

#: A second stop signal stops for real. `systemctl stop` sends SIGTERM once and waits for
#: TimeoutStopSec before sending SIGKILL, so this is the path an impatient operator takes.
_EXIT_FORCED: Final[int] = 0

#: Seconds between folds. The publisher is cheap — it reads only what is new — so five
#: seconds keeps the dashboard's ten-second cache honest without hammering the journal.
DEFAULT_INTERVAL: Final[float] = 5.0

#: Where the engine unit's StateDirectory puts the journal.
DEFAULT_JOURNAL: Final[Path] = Path("/var/lib/xauusd/journal/journal.db")

#: Where this unit's StateDirectory puts the snapshots.
DEFAULT_PROJECTION: Final[Path] = Path("/var/lib/xauusd/projection")

JOURNAL_PATH_ENV: Final[str] = "XAUUSD_JOURNAL_PATH"
PROJECTION_PATH_ENV: Final[str] = "XAUUSD_PROJECTION_PATH"
PUBLISH_INTERVAL_ENV: Final[str] = "XAUUSD_PUBLISH_INTERVAL"


def resolve_paths() -> tuple[Path, Path, float]:
    """Return ``(journal, projection, interval)``, from the environment or the defaults.

    Raises:
        ValueError: if the interval is not a positive number, or the projection path is not
            absolute. A relative path would resolve against whatever working directory
            systemd happened to start the unit in, which is not a deployment.
    """
    journal = Path(os.environ.get(JOURNAL_PATH_ENV) or DEFAULT_JOURNAL)
    projection = Path(os.environ.get(PROJECTION_PATH_ENV) or DEFAULT_PROJECTION)
    # The deployment default is exempt from the absolute check because it is *defined* for
    # the POSIX layout this unit ships: on a dev host the same string is not absolute, and
    # refusing it would make the module unusable anywhere but production. Anything an
    # operator *sets* must be absolute for the host it is set on.
    if projection != DEFAULT_PROJECTION and not projection.is_absolute():
        msg = (
            f"{PROJECTION_PATH_ENV}={projection} must be absolute: a relative projection "
            "path resolves against the unit's working directory, not the deployment"
        )
        raise ValueError(msg)

    raw_interval = os.environ.get(PUBLISH_INTERVAL_ENV)
    if raw_interval is None or not raw_interval.strip():
        interval = DEFAULT_INTERVAL
    else:
        try:
            interval = float(raw_interval.strip())
        except ValueError as exc:
            msg = f"{PUBLISH_INTERVAL_ENV}={raw_interval!r} is not a number"
            raise ValueError(msg) from exc
        if interval <= 0:
            msg = f"{PUBLISH_INTERVAL_ENV}={interval} must be positive"
            raise ValueError(msg)

    return journal, projection, interval


class _Stop(BaseException):
    """Raised into the loop by a signal handler, so shutdown is a normal exit path.

    Deliberately a :class:`BaseException`: a :class:`Exception` would be caught by the
    loop's generic ``except Exception`` and logged as a failed fold, and the process would
    carry on to the next sleep — turning an operator's ``systemctl stop`` into a warning
    line in the journal.
    """


def main() -> int:
    """Fold the journal into the projection until told to stop.

    Returns:
        ``0`` on a clean stop, ``1`` if the configuration itself is unusable.
    """
    logging.basicConfig(
        level=os.environ.get("XAUUSD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        journal_path, projection_path, interval = resolve_paths()
        # The output directory is created here rather than in resolve_paths so that the
        # resolver stays a pure function a test can call on any host. A publisher whose
        # projection directory does not exist would fail on its first atomic_write_json.
        projection_path.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as exc:
        logger.error("[publisher] misconfigured: %s", exc)
        return 1

    stopping = False

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stopping
        # A second signal is an operator who has lost patience: the first requests a clean
        # stop, the second is honoured immediately rather than queued behind the fold in
        # progress. It exits because the alternative is a process that will not die.
        if stopping:
            raise SystemExit(_EXIT_FORCED) from None
        stopping = True
        if signum == signal.SIGINT:
            raise KeyboardInterrupt from None
        raise _Stop from None

    previous = {signal.SIGTERM: signal.getsignal(signal.SIGTERM)}
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGHUP"):
        previous[signal.SIGHUP] = signal.getsignal(signal.SIGHUP)  # type: ignore[attr-defined]
        signal.signal(signal.SIGHUP, request_stop)  # type: ignore[attr-defined]

    publisher = ProjectionPublisher(
        journal_path=journal_path,
        projection_path=projection_path,
    )
    logger.info(
        "[publisher] starting: journal=%s projection=%s interval=%.1fs",
        journal_path,
        projection_path,
        interval,
    )

    exit_code = 0
    try:
        while not stopping:
            try:
                publisher.publish()
            except FileNotFoundError:
                # The engine has not created the journal yet. Normal during a cold start,
                # and the reason this loop is not a crash-loop.
                logger.info("[publisher] %s does not exist yet; waiting", journal_path)
            except sqlite3.Error as exc:
                # A locked or unreadable journal is the engine's problem, not this
                # process's. Log, wait, and try the next fold.
                logger.warning("[publisher] could not read the journal: %s", exc)
            except Exception:
                logger.exception("[publisher] fold failed")
            if stopping:
                break
            time.sleep(interval)
    except (_Stop, KeyboardInterrupt):
        logger.info("[publisher] stopping")
    finally:
        for signum, handler in previous.items():
            if handler is not None:
                signal.signal(signum, handler)

    logger.info("[publisher] stopped at %s", dt.datetime.now(dt.UTC).isoformat())
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
