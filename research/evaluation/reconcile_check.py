import sys
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

import datetime as dt
from decimal import Decimal
import json
from pathlib import Path
import numpy as np

from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner, SimulatedTrade
from research.strategies.enhanced_judas import EnhancedJudasStrategy
from research.metrics.trade_stats import compute_trade_metrics
from research.evaluation.followup_analysis import _trade_to_dict

print("=== QUESTION 1: V0 RECONCILIATION ===")
bars_val = load_all_m5_bars(years=["2020", "2021", "2022"])
runner = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"))
strat = EnhancedJudasStrategy()
trades_val, _ = runner.run_judas_backtest(bars_val, judas_strategy=strat)
t_dicts_val = [_trade_to_dict(t) for t in trades_val]

wins_val = sum(1 for t in t_dicts_val if t["r_multiple"] > 0)
losses_val = sum(1 for t in t_dicts_val if t["r_multiple"] < 0)
neutral_val = sum(1 for t in t_dicts_val if t["r_multiple"] == 0)

print(f"Total trades: {len(t_dicts_val)}")
print(f"Wins (> 0): {wins_val}, Losses (< 0): {losses_val}, Zero: {neutral_val}")
print(f"Raw win rate (wins / total): {wins_val / len(t_dicts_val):.4f}")

# Check with different bootstrap seeds
for seed in [0, 42, 123, 777, 999]:
    m = compute_trade_metrics(t_dicts_val, seed=seed)
    print(f"Seed {seed:3d}: win_rate={m['win_rate']:.4f}, CI=[{m['ci_95_lower']:.4f}, {m['ci_95_upper']:.4f}], exp={m['expectancy']:.4f}")

# Where did 18.86% come from?
# Look at 2023-2025 discovery set or original baseline:
bars_disc = load_all_m5_bars(years=["2023", "2024", "2025"])
strat_disc = EnhancedJudasStrategy()
trades_disc, _ = runner.run_judas_backtest(bars_disc, judas_strategy=strat_disc)
t_dicts_disc = [_trade_to_dict(t) for t in trades_disc]
wins_disc = sum(1 for t in t_dicts_disc if t["r_multiple"] > 0)
print(f"\nDiscovery (2023-2025) with EnhancedJudasStrategy:")
print(f"Total: {len(t_dicts_disc)}, Wins: {wins_disc}, WinRate: {wins_disc / len(t_dicts_disc):.4f}")

# Check core engine JudasSwingStrategy on 2023-2025
from engine.strategy.judas_swing import JudasSwingStrategy
core_strat = JudasSwingStrategy()
trades_core, _ = runner.run_judas_backtest(bars_disc, judas_strategy=core_strat)
t_dicts_core = [_trade_to_dict(t) for t in trades_core]
wins_core = sum(1 for t in t_dicts_core if t["r_multiple"] > 0)
print(f"\nDiscovery (2023-2025) with core JudasSwingStrategy:")
print(f"Total: {len(t_dicts_core)}, Wins: {wins_core}, WinRate: {wins_core / len(t_dicts_core):.4f}")
m_core = compute_trade_metrics(t_dicts_core, seed=42)
print(f"Core metrics: win_rate={m_core['win_rate']:.4f}, CI=[{m_core['ci_95_lower']:.4f}, {m_core['ci_95_upper']:.4f}], exp={m_core['expectancy']:.4f}")
