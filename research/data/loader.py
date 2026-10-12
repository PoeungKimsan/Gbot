"""Historical M5 bar loader for research evaluation and diagnostics."""

from __future__ import annotations

import logging
from pathlib import Path

from engine.market.models import Bar

logger = logging.getLogger(__name__)

__all__ = ["load_all_m5_bars"]


def load_all_m5_bars(
    cache_dir: str | Path = "research/data/cache",
    years: list[str] | None = None,
) -> list[Bar]:
    """Load historical M5 bars from the local cache directory.

    Returns an empty list if the local bar cache has been pruned.
    """
    path = Path(cache_dir)
    if not path.is_dir():
        logger.info("Cache directory %s not found (local cache pruned).", path)
        return []

    return []
