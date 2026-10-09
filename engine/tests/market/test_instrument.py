"""Unit tests for :mod:`engine.market.instrument`.

Covers tick/pip derivation, OANDA payload parsing, the YAML cache, the loader's
cache/fallback precedence, and the fixture used to keep tests off the network.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from engine.market.instrument import (
    InstrumentSpec,
    InstrumentSpecCache,
    InstrumentSpecError,
    InstrumentSpecLoader,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPO_ROOT / "engine" / "tests" / "fixtures" / "instruments" / "XAU_USD.yaml"


@pytest.fixture
def fixture_payload() -> dict:
    """The raw OANDA payload committed under ``engine/tests/fixtures``."""
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["raw"]


def _spec() -> InstrumentSpec:
    """A hand-built spec, used as the loader's offline fallback."""
    return InstrumentSpec(
        name="XAU_USD",
        pip_location=-4,
        display_precision=2,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )


# --------------------------------------------------------------------------- #
# construction and derived sizes
# --------------------------------------------------------------------------- #
def test_spec_is_frozen() -> None:
    spec = _spec()
    with pytest.raises(Exception):  # noqa: B017
        spec.name = "XAU_USD"  # type: ignore[misc]


def test_spec_is_hashable() -> None:
    spec = _spec()
    assert hash(spec) == hash(_spec())


@pytest.mark.parametrize(
    ("display_precision", "expected_tick"),
    [
        (2, Decimal("0.01")),
        (3, Decimal("0.001")),
        (5, Decimal("0.00001")),
        (8, Decimal("0.00000001")),
    ],
)
def test_tick_derived_from_display_precision(
    display_precision: int, expected_tick: Decimal
) -> None:
    spec = InstrumentSpec(
        name="XAU_USD",
        pip_location=-4,
        display_precision=display_precision,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )

    assert spec.tick == expected_tick


@pytest.mark.parametrize(
    ("pip_location", "expected_pip"),
    [(-4, Decimal("0.0001")), (-2, Decimal("0.01")), (0, Decimal("1"))],
)
def test_pip_derived_from_pip_location(pip_location: int, expected_pip: Decimal) -> None:
    spec = InstrumentSpec(
        name="XAU_USD",
        pip_location=pip_location,
        display_precision=2,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )

    assert spec.pip == expected_pip


def test_pip_may_be_finer_than_tick() -> None:
    """XAU_USD quotes at 0.01 but defines a pip as 0.0001.

    Asserting no ordering between the two is deliberate: a typical FX pair has a pip
    coarser than its tick, so an ordering check would be wrong for half the book.
    """
    gold = _spec()
    assert gold.pip < gold.tick

    euro = InstrumentSpec(
        name="EUR_USD",
        pip_location=-4,
        display_precision=5,
        trade_units_precision=0,
        minimum_trade_size=Decimal("1"),
    )
    assert euro.pip > euro.tick


@pytest.mark.parametrize("display_precision", [0, 1, 2, 3, 4, 5, 6, 7, 8])
def test_tick_is_exact_not_rounded(display_precision: int) -> None:
    """The power must not be silently rounded by the Decimal context.

    ``Decimal(10) ** -n`` is inexact once ``n`` passes the context precision, so the
    derived sizes assert their own exactness.
    """
    spec = InstrumentSpec(
        name="XAU_USD",
        pip_location=-4,
        display_precision=display_precision,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )

    assert spec.tick.as_tuple().exponent == -display_precision
    assert spec.pip.as_tuple().exponent == spec.pip_location


def test_zero_minimum_trade_size_rejected() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec(
            name="XAU_USD",
            pip_location=-4,
            display_precision=2,
            trade_units_precision=5,
            minimum_trade_size=Decimal("0"),
        )


def test_negative_minimum_trade_size_rejected() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec(
            name="XAU_USD",
            pip_location=-4,
            display_precision=2,
            trade_units_precision=5,
            minimum_trade_size=Decimal("-1"),
        )


def test_minimum_size_must_be_decimal() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec(
            name="XAU_USD",
            pip_location=-4,
            display_precision=2,
            trade_units_precision=5,
            minimum_trade_size="1",  # type: ignore[arg-type]
        )


def test_blank_name_rejected() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec(
            name="",
            pip_location=-4,
            display_precision=2,
            trade_units_precision=5,
            minimum_trade_size=Decimal("1"),
        )


@pytest.mark.parametrize("bad", [-1, 9])
def test_precision_bounds_rejected(bad: int) -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec(
            name="XAU_USD",
            pip_location=-4,
            display_precision=bad,
            trade_units_precision=5,
            minimum_trade_size=Decimal("1"),
        )


def test_pip_location_must_not_be_positive() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec(
            name="XAU_USD",
            pip_location=1,
            display_precision=2,
            trade_units_precision=5,
            minimum_trade_size=Decimal("1"),
        )


def test_non_int_precision_rejected() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec(
            name="XAU_USD",
            pip_location=-4,
            display_precision="2",  # type: ignore[arg-type]
            trade_units_precision=5,
            minimum_trade_size=Decimal("1"),
        )


# --------------------------------------------------------------------------- #
# OANDA payload parsing
# --------------------------------------------------------------------------- #
def test_from_oanda_payload(instrument_payload: dict) -> None:
    spec = InstrumentSpec.from_oanda_payload(instrument_payload)

    assert spec.name == "XAU_USD"
    assert spec.pip_location == -4
    assert spec.display_precision == 2
    assert spec.trade_units_precision == 5
    assert spec.minimum_trade_size == Decimal("1")
    assert spec.tick == Decimal("0.01")
    assert spec.pip == Decimal("0.0001")


def test_from_oanda_payload_accepts_numeric_strings() -> None:
    spec = InstrumentSpec.from_oanda_payload(
        {
            "name": "XAU_USD",
            "pipLocation": "-4",
            "displayPrecision": "2",
            "tradeUnitsPrecision": "5",
            "minimumTradeSize": "1",
        }
    )

    assert spec.tick == Decimal("0.01")
    assert spec.minimum_trade_size == Decimal("1")


def test_from_oanda_payload_rejects_float_precision() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec.from_oanda_payload(
            {
                "name": "XAU_USD",
                "pipLocation": -4,
                "displayPrecision": 2.5,
                "tradeUnitsPrecision": 5,
                "minimumTradeSize": "1",
            }
        )


def test_from_oanda_payload_rejects_non_mapping() -> None:
    with pytest.raises(InstrumentSpecError):
        InstrumentSpec.from_oanda_payload(["not", "a", "mapping"])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "missing",
    ["name", "pipLocation", "displayPrecision", "tradeUnitsPrecision", "minimumTradeSize"],
)
def test_from_oanda_payload_rejects_missing_field(
    instrument_payload: dict, missing: str
) -> None:
    del instrument_payload[missing]

    with pytest.raises(InstrumentSpecError):
        InstrumentSpec.from_oanda_payload(instrument_payload)


def test_from_oanda_payload_rejects_bad_minimum_trade_size(instrument_payload: dict) -> None:
    instrument_payload["minimumTradeSize"] = "not-a-number"

    with pytest.raises(InstrumentSpecError):
        InstrumentSpec.from_oanda_payload(instrument_payload)


def test_roundtrip_through_payload() -> None:
    payload = {
        "name": "XAU_USD",
        "pipLocation": -4,
        "displayPrecision": 2,
        "tradeUnitsPrecision": 5,
        "minimumTradeSize": "1",
    }

    expected = InstrumentSpec.from_oanda_payload(payload)

    assert (
        InstrumentSpec.from_oanda_payload(expected.to_oanda_payload()) == expected
    )


# --------------------------------------------------------------------------- #
# YAML cache
# --------------------------------------------------------------------------- #
def test_cache_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "instrument.cache.yaml"
    cache = InstrumentSpecCache(path)

    cache.write(_spec())

    assert path.is_file()
    assert cache.read() == _spec()


def test_cache_missing_file_returns_none(tmp_path: Path) -> None:
    cache = InstrumentSpecCache(tmp_path / "absent.yaml")

    assert cache.read() is None


def test_cache_does_not_use_floats(tmp_path: Path) -> None:
    """The cache file must persist sizes as exact strings, never YAML floats.

    Parsing a float back would lose the exactness the Decimal rules exist to keep.
    """
    path = tmp_path / "instrument.cache.yaml"
    InstrumentSpecCache(path).write(_spec())

    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert payload["minimumTradeSize"] == "1"
    for key, value in payload.items():
        assert not isinstance(value, float), f"{key} was persisted as a float"

    assert InstrumentSpecCache(path).read() == _spec()


def test_cache_corrupt_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "instrument.cache.yaml"
    path.write_text("name: XAU_USD\n  bad indent: [", encoding="utf-8")
    cache = InstrumentSpecCache(path)

    with pytest.raises(InstrumentSpecError):
        cache.read()


def test_cache_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "instrument.cache.yaml"
    path.write_text("name: XAU_USD\nbogus: 1\n", encoding="utf-8")
    cache = InstrumentSpecCache(path)

    with pytest.raises(InstrumentSpecError):
        cache.read()


def test_cache_staleness_uses_injected_clock(tmp_path: Path) -> None:
    path = tmp_path / "instrument.cache.yaml"
    written_at = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
    later = written_at + dt.timedelta(hours=6)

    cache = InstrumentSpecCache(path, ttl=dt.timedelta(hours=24))
    cache.write(_spec(), fetched_at=written_at)

    assert cache.is_stale(now=later) is False
    assert cache.is_stale(now=written_at + dt.timedelta(hours=25)) is True


def test_cache_is_stale_when_missing(tmp_path: Path) -> None:
    cache = InstrumentSpecCache(tmp_path / "absent.yaml")

    assert cache.read() is None
    assert cache.is_stale() is True


# --------------------------------------------------------------------------- #
# loader: cache / fetch / fallback precedence
# --------------------------------------------------------------------------- #
class _FakeFetcher:
    """Records calls and returns the committed fixture payload."""

    def __init__(self, payload: dict, calls: list[str]) -> None:
        self._payload = payload
        self.calls = calls

    async def __call__(self) -> dict:
        self.calls.append("fetch")
        return self._payload


async def test_loader_writes_and_returns_cached_spec(
    tmp_path: Path, fixture_payload: dict
) -> None:
    calls: list[str] = []
    path = tmp_path / "instrument.cache.yaml"
    loader = InstrumentSpecLoader(
        fetcher=_FakeFetcher(fixture_payload, calls),
        cache=InstrumentSpecCache(path),
    )

    spec = await loader.load()

    assert spec == InstrumentSpec.from_oanda_payload(fixture_payload)
    assert calls == ["fetch"]
    assert InstrumentSpecCache(path).read() == spec


async def test_loader_uses_fresh_cache_without_fetching(
    tmp_path: Path, fixture_payload: dict
) -> None:
    calls: list[str] = []
    path = tmp_path / "instrument.cache.yaml"
    written_at = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)

    def clock() -> dt.datetime:
        return dt.datetime(2026, 1, 1, 18, 0, 0, tzinfo=dt.UTC)

    InstrumentSpecCache(path, ttl=dt.timedelta(hours=24)).write(
        _spec(), fetched_at=written_at
    )
    loader = InstrumentSpecLoader(
        fetcher=_FakeFetcher(fixture_payload, calls),
        cache=InstrumentSpecCache(path, ttl=dt.timedelta(hours=24)),
        clock=clock,
    )

    spec = await loader.load()

    assert spec == _spec()
    assert calls == [], "a fresh cache must not hit the network"


async def test_loader_refetches_when_cache_is_stale(
    tmp_path: Path, fixture_payload: dict
) -> None:
    calls: list[str] = []
    path = tmp_path / "instrument.cache.yaml"
    written_at = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)

    def clock() -> dt.datetime:
        return dt.datetime(2026, 1, 3, 12, 0, 0, tzinfo=dt.UTC)

    InstrumentSpecCache(path, ttl=dt.timedelta(hours=24)).write(
        _spec(), fetched_at=written_at
    )
    loader = InstrumentSpecLoader(
        fetcher=_FakeFetcher(fixture_payload, calls),
        cache=InstrumentSpecCache(path, ttl=dt.timedelta(hours=24)),
        clock=clock,
    )

    spec = await loader.load(force_refresh=False)

    assert spec == InstrumentSpec.from_oanda_payload(fixture_payload)
    assert calls == ["fetch"]


async def test_loader_force_refresh_ignores_fresh_cache(
    tmp_path: Path, fixture_payload: dict
) -> None:
    calls: list[str] = []
    path = tmp_path / "instrument.cache.yaml"
    InstrumentSpecCache(path).write(_spec())
    loader = InstrumentSpecLoader(
        fetcher=_FakeFetcher(fixture_payload, calls),
        cache=InstrumentSpecCache(path),
    )

    spec = await loader.load(force_refresh=True)

    assert spec == InstrumentSpec.from_oanda_payload(fixture_payload)
    assert calls == ["fetch"]


async def test_loader_falls_back_when_fetch_fails(
    tmp_path: Path, fixture_payload: dict
) -> None:
    class _Boom:
        async def __call__(self) -> dict:
            raise RuntimeError("oanda unavailable")

    loader = InstrumentSpecLoader(
        fetcher=_Boom(),  # type: ignore[arg-type]
        cache=InstrumentSpecCache(tmp_path / "absent.yaml"),
        fallback=_spec(),
    )

    assert await loader.load() == _spec()


async def test_loader_raises_without_fetch_and_without_fallback(tmp_path: Path) -> None:
    class _Boom:
        async def __call__(self) -> dict:
            raise RuntimeError("oanda unavailable")

    loader = InstrumentSpecLoader(
        fetcher=_Boom(),  # type: ignore[arg-type]
        cache=InstrumentSpecCache(tmp_path / "absent.yaml"),
    )

    with pytest.raises(InstrumentSpecError):
        await loader.load()


async def test_loader_uses_fallback_when_cache_and_fetch_both_fail(tmp_path: Path) -> None:
    class _Boom:
        async def __call__(self) -> dict:
            raise RuntimeError("oanda unavailable")

    path = tmp_path / "instrument.cache.yaml"
    path.write_text("garbage: [", encoding="utf-8")

    loader = InstrumentSpecLoader(
        fetcher=_Boom(),  # type: ignore[arg-type]
        cache=InstrumentSpecCache(path),
        fallback=_spec(),
    )

    assert await loader.load() == _spec()


async def test_fixture_fallback_matches_committed_yaml(fixture_payload: dict) -> None:
    """The committed fixture must parse into a spec matching its derived sizes."""
    spec = InstrumentSpec.from_oanda_payload(fixture_payload)

    assert spec.tick == Decimal("0.01")
    assert spec.pip == Decimal("0.0001")


def test_fixture_payload_agrees_with_fixture_fields() -> None:
    fixture = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert fixture["raw"]["name"] == fixture["name"]
    assert fixture["raw"]["displayPrecision"] == fixture["displayPrecision"]
    assert fixture["raw"]["minimumTradeSize"] == fixture["minimumTradeSize"]
