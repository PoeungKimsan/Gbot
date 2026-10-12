"""Frozen cost and friction model for trend research."""

from __future__ import annotations

import datetime as dt

PREREG_HASH = "d8330268db3e5e0115751e4d2a7c1fc06c01451448d473bc9cca35f45dd075c3"
PREREG_COMMIT = "f1affec"
REGISTRATION_VERSION = "trend_v1.2"

# Frozen spread table with 1.5x safety multiplier
FROZEN_SPREAD_TABLE: dict[str, float] = {
    "EURUSD": 0.00015,
    "GBPUSD": 0.00018,
    "USDJPY": 0.015,
    "AUDUSD": 0.00018,
    "USDCAD": 0.000225,
    "USDCHF": 0.000225,
    "XAUUSD": 0.375,
    "XAGUSD": 0.030,
    "US500": 0.75,
    "GER40": 2.25,
    "Brent": 0.06,
    "NDX": 2.25,
}

PRIMARY_FINANCING_RATE = 0.02  # 2.0% annual financing rate
SENSITIVITY_FIN0_RATE = 0.00   # 0.0% annual financing rate
SENSITIVITY_FIN4_RATE = 0.04   # 4.0% annual financing rate
STOP_SLIPPAGE_MULTIPLIER = 0.5  # 0.5x spread on stop loss exits


def get_spread(symbol: str, spread_multiplier: float = 1.0) -> float:
    base = FROZEN_SPREAD_TABLE.get(symbol)
    if base is None:
        raise ValueError(f"Unknown symbol for spread table: {symbol}")
    return base * spread_multiplier


def compute_costs(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    stop_distance: float,
    is_stop: bool,
    entry_date: dt.date,
    exit_date: dt.date,
    spread_multiplier: float = 1.0,
    annual_financing_rate: float = PRIMARY_FINANCING_RATE,
) -> dict[str, float]:
    """Compute exact gross and net R, points drag, and sensitivities for a trade."""
    if stop_distance <= 0:
        raise ValueError(f"Stop distance must be positive, got {stop_distance}")

    spread = get_spread(symbol, spread_multiplier=1.0)
    effective_spread = spread * spread_multiplier

    # Slippage on stop exit: 0.5 * spread
    slippage = (STOP_SLIPPAGE_MULTIPLIER * spread) if is_stop else 0.0

    # Holding duration in calendar days (min 1 day)
    holding_days = max((exit_date - entry_date).days, 1)

    # Financing cost per unit in price points:
    # notional = entry_price
    # dollar/pts financing = entry_price * rate * (days / 365)
    financing_pts_primary = entry_price * PRIMARY_FINANCING_RATE * (holding_days / 365.0)
    financing_pts_current = entry_price * annual_financing_rate * (holding_days / 365.0)
    financing_pts_fin0 = 0.0
    financing_pts_fin4 = entry_price * SENSITIVITY_FIN4_RATE * (holding_days / 365.0)

    # Gross PnL in points
    if direction == "LONG":
        gross_pnl_pts = exit_price - entry_price
    elif direction == "SHORT":
        gross_pnl_pts = entry_price - exit_price
    else:
        raise ValueError(f"Unknown direction: {direction}")

    gross_r = gross_pnl_pts / stop_distance

    # R drag
    drag_spread_r = effective_spread / stop_distance
    drag_slippage_r = slippage / stop_distance
    drag_financing_r = financing_pts_current / stop_distance

    drag_financing_r_primary = financing_pts_primary / stop_distance
    drag_financing_r_fin0 = financing_pts_fin0 / stop_distance
    drag_financing_r_fin4 = financing_pts_fin4 / stop_distance

    # Spread x2 drag
    drag_spread2x_r = (spread * 2.0) / stop_distance
    drag_slippage2x_r = (STOP_SLIPPAGE_MULTIPLIER * spread * 2.0) / stop_distance if is_stop else 0.0

    net_r = gross_r - drag_spread_r - drag_slippage_r - drag_financing_r
    net_r_fin0 = gross_r - (spread / stop_distance) - drag_slippage_r - drag_financing_r_fin0
    net_r_fin4 = gross_r - (spread / stop_distance) - drag_slippage_r - drag_financing_r_fin4
    net_r_spread2x = gross_r - drag_spread2x_r - drag_slippage2x_r - drag_financing_r_primary

    return {
        "gross_pnl_pts": gross_pnl_pts,
        "gross_r": gross_r,
        "spread_cost_pts": effective_spread,
        "slippage_cost_pts": slippage,
        "financing_cost_pts": financing_pts_current,
        "drag_spread_r": drag_spread_r,
        "drag_slippage_r": drag_slippage_r,
        "drag_financing_r": drag_financing_r,
        "net_r": net_r,
        "net_r_fin0": net_r_fin0,
        "net_r_fin4": net_r_fin4,
        "net_r_spread2x": net_r_spread2x,
        "holding_days": holding_days,
    }
