import sys
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

from decimal import Decimal
from engine.market.instrument import InstrumentSpec
from engine.market.models import Bar
from research.data.loader import load_all_m5_bars
from research.backtest.engine import BacktestRunner
from research.adapters.silver_bullet_adapter import load_silver_bullet_strategy

spec = InstrumentSpec("XAU_USD", -2, 2, 2, Decimal("0.01"))
runner = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"))

# 1. 2020-2022
print("=== SILVER BULLET 2020-2022 ===")
bars_val = load_all_m5_bars(years=["2020", "2021", "2022"])
strat_val, _ = load_silver_bullet_strategy(spec)
orig_val = strat_val.on_bar
def p_val(b: Bar):
    res = orig_val(b)
    idx = strat_val._detector.bar_count
    if idx > 400:
        strat_val._detector.drop_levels(idx - 400)
    return res
strat_val.on_bar = p_val

trades_val, stats_val = runner.run_silver_bullet_backtest(bars_val, strat_val)
wins_val = sum(1 for t in trades_val if t.r_multiple > 0)
losses_val = sum(1 for t in trades_val if t.r_multiple < 0)

print(f"Data file: research/data/cache/m5_bars_2020_2021_2022.pkl")
print(f"Date range of bars: {bars_val[0].timestamp_utc.isoformat()} to {bars_val[-1].timestamp_utc.isoformat()}")
print(f"Total bars: {len(bars_val)}")
print(f"Trades count N: {len(trades_val)}")
print(f"Wins: {wins_val}, Losses: {losses_val}")
if trades_val:
    print(f"First trade entry: {trades_val[0].entry_time.isoformat()}")
    print(f"Last trade entry: {trades_val[-1].entry_time.isoformat()}")

# 2. 2023-2025
print("\n=== SILVER BULLET 2023-2025 ===")
bars_disc = load_all_m5_bars(years=["2023", "2024", "2025"])
strat_disc, _ = load_silver_bullet_strategy(spec)
orig_disc = strat_disc.on_bar
def p_disc(b: Bar):
    res = orig_disc(b)
    idx = strat_disc._detector.bar_count
    if idx > 400:
        strat_disc._detector.drop_levels(idx - 400)
    return res
strat_disc.on_bar = p_disc

trades_disc, stats_disc = runner.run_silver_bullet_backtest(bars_disc, strat_disc)
wins_disc = sum(1 for t in trades_disc if t.r_multiple > 0)
losses_disc = sum(1 for t in trades_disc if t.r_multiple < 0)

print(f"Data file: research/data/cache/m5_bars_2023_2024_2025.pkl")
print(f"Date range of bars: {bars_disc[0].timestamp_utc.isoformat()} to {bars_disc[-1].timestamp_utc.isoformat()}")
print(f"Total bars: {len(bars_disc)}")
print(f"Trades count N: {len(trades_disc)}")
print(f"Wins: {wins_disc}, Losses: {losses_disc}")
if trades_disc:
    print(f"First trade entry: {trades_disc[0].entry_time.isoformat()}")
    print(f"Last trade entry: {trades_disc[-1].entry_time.isoformat()}")
