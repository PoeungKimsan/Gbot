"""The strategy document, and the digest that names it.

``config/strategy.yaml`` holds every tunable the detectors and the silver bullet
read, and it is loaded through :mod:`engine.risk.decimal_yaml` for the same reason
the risk document is: YAML hands back a binary float for an unquoted ``0.25``, and
a threshold that was silently rounded at load time is a strategy nobody tested.

The *version* is why this module exists next to the detectors rather than inside
them. A run stamps the SHA-256 of the parsed document into the journal header, so
two runs are only comparable when they ran the same policy, and a rerun under a
changed threshold is a different strategy by definition. The digest is taken over
the *canonical* JSON of the parsed document, which means key order and blank lines
do not move it and every edited value does.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from engine.domain.events import DomainEvent, EventType, canonical_json_bytes
from engine.risk.decimal_yaml import decimal_scalar, load_decimal_yaml
from engine.risk.rounding import RoundingError, require_decimal

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_STRATEGY_CONFIG_PATH",
    "STRATEGY_VERSION_ALGORITHM",
    "StrategyConfig",
    "StrategyConfigError",
    "StrategyVersion",
    "load_strategy_config",
    "run_header_event",
    "strategy_version_of",
]

#: The committed strategy document, relative to the repository root.
DEFAULT_STRATEGY_CONFIG_PATH: Final[Path] = Path("config") / "strategy.yaml"

#: The digest recorded as ``strategy_version`` in the journal header.
STRATEGY_VERSION_ALGORITHM: Final[str] = "sha256"

#: Counts. They index bars, so they are integers or nothing.
_INTEGER_FIELDS: Final[tuple[str, ...]] = (
    "swing_n",
    "sweep_min_ticks",
    "sweep_close_back_bars",
    "atr_window",
    "order_expiry_bars",
    "stop_buffer_ticks",
)

#: Thresholds multiplied against prices, so they are exact Decimals.
_DECIMAL_FIELDS: Final[tuple[str, ...]] = ("mss_body_k", "fvg_min_atr", "rr_target")

#: The one count that may be zero: a stop pinned exactly at the swept extreme is a
#: choice, not a defect.
_NON_NEGATIVE_COUNTS: Final[frozenset[str]] = frozenset({"stop_buffer_ticks"})


class StrategyConfigError(ValueError):
    """The strategy document, or a value derived from it, is out of policy."""


@dataclasses.dataclass(frozen=True, slots=True)
class StrategyConfig:
    """Every tunable the Phase 4 detectors and the silver bullet share.

    A ``float`` never reaches a field: an unquoted ``0.25`` in the YAML is refused
    at load time, and a ``float`` passed through :meth:`from_mapping` is refused
    here, because a threshold that was silently rounded is a strategy nobody
    tested.
    """

    swing_n: int
    sweep_min_ticks: int
    sweep_close_back_bars: int
    atr_window: int
    mss_body_k: Decimal
    fvg_min_atr: Decimal
    order_expiry_bars: int
    stop_buffer_ticks: int
    rr_target: Decimal

    def __post_init__(self) -> None:
        for name in _INTEGER_FIELDS:
            _validated_count(name, getattr(self, name))
        for name in _DECIMAL_FIELDS:
            _validated_threshold(name, getattr(self, name))

    # -- loading ------------------------------------------------------------- #
    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> StrategyConfig:
        """Build from an already float-checked YAML mapping.

        Args:
            document: The parsed ``strategy.yaml`` mapping.

        Returns:
            The validated :class:`StrategyConfig`.

        Raises:
            StrategyConfigError: if a key is absent, or a value is out of policy.
        """
        if not isinstance(document, dict):
            raise StrategyConfigError(
                f"strategy config must be a mapping, got {type(document).__name__}"
            )

        fields: dict[str, Any] = {
            name: _required_int(document, name) for name in _INTEGER_FIELDS
        }
        fields.update({name: _required_decimal(document, name) for name in _DECIMAL_FIELDS})
        return cls(**fields)


def _validated_count(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StrategyConfigError(f"{name} must be an int, got {type(value).__name__}")
    if name in _NON_NEGATIVE_COUNTS:
        if value < 0:
            raise StrategyConfigError(f"{name} must not be negative, got {value}")
    elif value <= 0:
        raise StrategyConfigError(f"{name} must be positive, got {value}")
    return value


def _validated_threshold(name: str, value: object) -> Decimal:
    try:
        return require_decimal(value, field=name, positive=True)
    except RoundingError as exc:
        raise StrategyConfigError(str(exc)) from exc


def _required_int(document: Mapping[str, Any], key: str) -> int:
    if key not in document:
        raise StrategyConfigError(f"missing '{key}'")
    return _validated_count(key, document[key])


def _required_decimal(document: Mapping[str, Any], key: str) -> Decimal:
    if key not in document:
        raise StrategyConfigError(f"missing '{key}'")
    try:
        return decimal_scalar(document, key)
    except ValueError as exc:
        raise StrategyConfigError(f"'{key}' is not a usable decimal: {exc}") from exc


def load_strategy_config(path: Path | str = DEFAULT_STRATEGY_CONFIG_PATH) -> StrategyConfig:
    """Load and validate the strategy document.

    Args:
        path: Path to ``config/strategy.yaml``.

    Returns:
        The parsed :class:`StrategyConfig`.

    Raises:
        StrategyConfigError: if the file is unreadable, carries a float, or holds a
            value that is out of policy.
    """
    document = load_decimal_yaml(path)
    try:
        return StrategyConfig.from_mapping(document)
    except StrategyConfigError as exc:
        raise StrategyConfigError(f"{path}: {exc}") from exc


@dataclasses.dataclass(frozen=True, slots=True)
class StrategyVersion:
    """The identity of the policy a run executed under.

    ``digest`` is the SHA-256 of the parsed document's canonical JSON, so it changes
    exactly when a value does. ``config_path`` is recorded so a reader can find the
    document the digest came from without trusting a mapping in the payload.
    """

    digest: bytes
    config_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.digest, bytes):
            raise StrategyConfigError(
                f"strategy version digest must be bytes, got {type(self.digest).__name__}"
            )
        if not isinstance(self.config_path, str) or not self.config_path.strip():
            raise StrategyConfigError(
                f"strategy version config_path must be a non-empty string, got "
                f"{self.config_path!r}"
            )

    @property
    def algorithm(self) -> str:
        return STRATEGY_VERSION_ALGORITHM

    @property
    def hex_digest(self) -> str:
        return self.digest.hex()

    def __str__(self) -> str:
        return self.hex_digest

    def header_payload(self) -> dict[str, str]:
        """The payload a run's first journal event carries.

        Only strings, so the header canonicalizes to a stable byte sequence and no
        Decimal ever reaches the journal's amount column from a config.
        """
        return {
            "strategy_version": self.hex_digest,
            "strategy_version_algorithm": self.algorithm,
            "strategy_config_path": self.config_path,
        }


def strategy_version_of(document: Mapping[str, Any], *, config_path: str) -> StrategyVersion:
    """Digest an already float-checked strategy document.

    Args:
        document: The parsed ``strategy.yaml`` mapping.
        config_path: Where the document was read from, recorded alongside the digest.

    Returns:
        The :class:`StrategyVersion` for that document.
    """
    if not isinstance(document, dict):
        raise StrategyConfigError(
            f"strategy document must be a mapping, got {type(document).__name__}"
        )
    return StrategyVersion(
        digest=hashlib.sha256(canonical_json_bytes(dict(document))).digest(),
        config_path=config_path,
    )


def run_header_event(
    version: StrategyVersion, *, run_id: str, at: dt.datetime
) -> DomainEvent:
    """Build the journal header event for a run.

    ``RUN_STARTED`` is the first event a run writes, so its payload is the header:
    whatever a reader needs to know before replaying anything after it.
    """
    if not isinstance(version, StrategyVersion):
        raise StrategyConfigError(
            f"version must be a StrategyVersion, got {type(version).__name__}"
        )
    if not isinstance(run_id, str) or not run_id.strip():
        raise StrategyConfigError(f"run_id must be a non-empty string, got {run_id!r}")
    if not isinstance(at, dt.datetime) or at.tzinfo is None:
        raise StrategyConfigError(f"at must be timezone-aware, got {at!r}")

    return DomainEvent(
        event_id=f"{run_id}-strategy-version",
        event_type=EventType.RUN_STARTED,
        occurred_at=at,
        payload={"run_id": run_id, **version.header_payload()},
    )
