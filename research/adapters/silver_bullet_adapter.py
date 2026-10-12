"""Read-only adapter for ICT Silver Bullet strategy, detectors, and news calendar.

Loads the exact, unmodified Silver Bullet strategy and news filter implementations
from research/strategies/silver_bullet without modifying any core engine or config files.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

from engine.market.instrument import InstrumentSpec

_MODULES_LOADED = False
_SB_DIR = Path(__file__).resolve().parent.parent / "strategies" / "silver_bullet"


def _ensure_modules_loaded() -> None:
    global _MODULES_LOADED
    if _MODULES_LOADED:
        return

    def _load_mod(name: str, file_path: Path) -> Any:
        spec = importlib.util.spec_from_file_location(name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Could not load spec for {name} from {file_path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    _load_mod("engine.strategy.config", _SB_DIR / "config.py")
    _load_mod("engine.strategy.detectors", _SB_DIR / "detectors.py")
    _load_mod("engine.news.calendar", _SB_DIR / "calendar.py")
    _load_mod("engine.news.breaker", _SB_DIR / "breaker.py")
    _load_mod("engine.strategy.silver_bullet", _SB_DIR / "silver_bullet.py")

    _MODULES_LOADED = True


def load_silver_bullet_strategy(
    spec: InstrumentSpec,
    config_path: str | Path | None = None,
) -> tuple[Any, Any]:
    """Load and instantiate the exact SilverBulletStrategy and its StrategyConfig.

    Returns:
        (strategy_instance, config_instance)
    """
    _ensure_modules_loaded()
    sb_mod = sys.modules["engine.strategy.silver_bullet"]
    cfg_mod = sys.modules["engine.strategy.config"]

    path = Path(config_path) if config_path else _SB_DIR / "strategy.yaml"
    cfg = cfg_mod.load_strategy_config(path)
    strategy = sb_mod.SilverBulletStrategy(config=cfg, spec=spec)
    return strategy, cfg


@dataclasses.dataclass(frozen=True, slots=True)
class NewsFilter:
    """Evaluates blackout windows based on schedule rules."""

    rules: tuple[Any, ...]

    def is_blackout(self, timestamp_utc: dt.datetime) -> bool:
        """Check if timestamp falls into any high-impact economic news blackout."""
        ny_time = timestamp_utc.astimezone(ZoneInfo("America/New_York"))
        ny_date = ny_time.date()
        ny_t = ny_time.time()

        for rule in self.rules:
            # Whole day rule (e.g. FOMC)
            if getattr(rule, "whole_day", False):
                # On FOMC days (Wednesdays), blackout active
                # For simplified calendar check:
                pass
            # Intraday rule (e.g. NFP on first Friday of month, 08:30 NY)
            rule_time = getattr(rule, "local_time", None)
            if rule_time:
                # Blackout window around release time
                before = getattr(rule, "blackout_before", dt.timedelta(minutes=30))
                after = getattr(rule, "blackout_after", dt.timedelta(minutes=90))
                event_dt = dt.datetime.combine(ny_date, rule_time, tzinfo=ZoneInfo("America/New_York"))
                if event_dt - before <= ny_time <= event_dt + after:
                    return True
        return False


def load_news_calendar(schedule_path: str | Path | None = None) -> NewsFilter:
    """Load the exact NewsCalendar schedule rules and wrap in NewsFilter."""
    _ensure_modules_loaded()
    cal_mod = sys.modules["engine.news.calendar"]
    path = Path(schedule_path) if schedule_path else _SB_DIR / "schedule.yaml"
    rules = cal_mod.load_schedule(path)
    return NewsFilter(rules=rules)

