import sys
sys.path.insert(0, "engine/src")
sys.path.insert(0, ".")

import datetime as dt
from decimal import Decimal
import numpy as np
from zoneinfo import ZoneInfo

from engine.market.instrument import InstrumentSpec
from engine.market.models import Bar
from research.adapters.silver_bullet_adapter import load_silver_bullet_strategy
from research.backtest.engine import BacktestRunner, RestingLimit, SimulatedTrade
from research.data.loader import load_all_m5_bars
from research.metrics.trade_stats import compute_trade_metrics
from research.evaluation.followup_analysis import _trade_to_dict

NY_TZ = ZoneInfo("America/New_York")
spec = InstrumentSpec("XAU_USD", -2, 2, 2, Decimal("0.01"))
runner = BacktestRunner(base_spread=Decimal("0.20"), base_slippage=Decimal("0.05"))

# =========================================================================
# PART A: Split 29 Unfilled Setups on 2020-2022
# =========================================================================
print("=== PART A: 2020-2022 UNFILLED SETUPS DECOMPOSITION ===")
bars_20_22 = load_all_m5_bars(years=["2020", "2021", "2022"])
strat_20_22, _ = load_silver_bullet_strategy(spec)
orig_sb = strat_20_22.on_bar
def p_sb(b: Bar):
    res = orig_sb(b)
    idx = strat_20_22._detector.bar_count
    if idx > 400:
        strat_20_22._detector.drop_levels(idx - 400)
    return res
strat_20_22.on_bar = p_sb

resting_orders: list[RestingLimit] = []
active_trade: SimulatedTrade | None = None
unfilled_orders: list[tuple[RestingLimit, str]] = [] # order, category: 'never_reached' or 'touched'
filled_trades: list[SimulatedTrade] = []

for idx, bar in enumerate(bars_20_22):
    bar_time = bar.timestamp_utc
    ny_time = bar_time.astimezone(NY_TZ)
    ny_date = ny_time.date()
    mid = bar.mid_ohlc

    # 1. Manage trade
    if active_trade is not None:
        stop_hit = (mid.low <= active_trade.stop_loss) if active_trade.side == "BUY" else (mid.high >= active_trade.stop_loss)
        tp_hit = (mid.high >= active_trade.take_profit) if active_trade.side == "BUY" else (mid.low <= active_trade.take_profit)
        dynamic_close = ny_time.time() >= dt.time(16, 45)
        if stop_hit or tp_hit or dynamic_close:
            active_trade = None

    # 2. Check resting orders
    for order in list(resting_orders):
        if active_trade is not None:
            break

        # Check expiry
        if idx > order.expires_bar_idx or ny_time.time() >= dt.time(16, 45):
            resting_orders.remove(order)
            # Determine if touched or never reached during its resting lifetime
            # Resting window was [order.placed_bar_idx, idx - 1]
            touched = False
            for w_i in range(order.placed_bar_idx, idx):
                w_mid = bars_20_22[w_i].mid_ohlc
                if order.side == "BUY":
                    if w_mid.low <= order.limit_price:
                        touched = True
                        break
                else:
                    if w_mid.high >= order.limit_price:
                        touched = True
                        break
            cat = "touched_not_through" if touched else "never_reached"
            unfilled_orders.append((order, cat))
            continue

        # Check through fill
        filled = (mid.low < order.limit_price) if order.side == "BUY" else (mid.high > order.limit_price)
        if filled:
            resting_orders.remove(order)
            active_trade = SimulatedTrade(
                trade_id=f"sb-{len(filled_trades)+1}",
                strategy="ICT Silver Bullet",
                side=order.side,
                entry_time=bar_time,
                entry_price=order.limit_price,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                risk_dollars=abs(order.limit_price - order.stop_loss),
                reward_dollars=abs(order.take_profit - order.limit_price),
            )
            filled_trades.append(active_trade)
            break

    # 3. New intents
    intents = strat_20_22.on_bar(bar)
    for intent in intents:
        if type(intent).__name__ == "PlaceLimit":
            side_str = "BUY" if intent.side.value == "BUY" else "SELL"
            resting_orders.append(
                RestingLimit(
                    order_id=intent.order_id,
                    strategy="ICT Silver Bullet",
                    side=side_str,
                    limit_price=intent.limit_price,
                    stop_loss=intent.stop_price,
                    take_profit=intent.target_price,
                    placed_time=bar_time,
                    placed_bar_idx=idx,
                    expires_bar_idx=idx + intent.expires_after_bars,
                    in_sample=True,
                )
            )

print(f"Total filled trades: {len(filled_trades)}")
print(f"Total unfilled orders: {len(unfilled_orders)}")

cat_never = [ord_obj for ord_obj, cat in unfilled_orders if cat == "never_reached"]
cat_touch = [ord_obj for ord_obj, cat in unfilled_orders if cat == "touched_not_through"]

print(f"  (a) Price NEVER reached limit: {len(cat_never)}")
print(f"  (b) Price TOUCHED but not through: {len(cat_touch)}")

# Forward trace for both categories
def trace_hypothetical(orders_list, bars):
    wins = 0
    losses = 0
    r_list = []
    for ord_obj in orders_list:
        side = ord_obj.side
        start_idx = ord_obj.placed_bar_idx
        entry = ord_obj.limit_price
        sl = ord_obj.stop_loss
        tp = ord_obj.take_profit
        risk = abs(entry - sl)
        target = abs(tp - entry)
        r_tgt = float(target / risk) if risk > 0 else 2.0

        outcome = None
        for f_i in range(start_idx + 1, min(len(bars), start_idx + 288)):
            f_mid = bars[f_i].mid_ohlc
            hit_sl = f_mid.low <= sl if side == "BUY" else f_mid.high >= sl
            hit_tp = f_mid.high >= tp if side == "BUY" else f_mid.low <= tp
            if hit_sl and hit_tp:
                outcome = -1.0
                break
            elif hit_sl:
                outcome = -1.0
                break
            elif hit_tp:
                outcome = r_tgt
                break
        if outcome is None:
            last_c = bars[min(len(bars)-1, start_idx + 287)].mid_ohlc.close
            diff = (last_c - entry) if side == "BUY" else (entry - last_c)
            outcome = float(diff / risk) if risk > 0 else 0.0

        if outcome > 0:
            wins += 1
        elif outcome < 0:
            losses += 1
        r_list.append(outcome)
    n = len(orders_list)
    wr = (wins / n * 100) if n > 0 else 0.0
    avg_r = float(np.mean(r_list)) if r_list else 0.0
    return {"n": n, "wins": wins, "losses": losses, "win_rate": wr, "avg_r": avg_r, "r_list": r_list}

res_never = trace_hypothetical(cat_never, bars_20_22)
res_touch = trace_hypothetical(cat_touch, bars_20_22)

print(f"Outcomes for (a) NEVER REACHED (N={res_never['n']}): Wins={res_never['wins']}, Losses={res_never['losses']}, WR={res_never['win_rate']:.2f}%, Avg R={res_never['avg_r']:.4f}R")
print(f"Outcomes for (b) TOUCHED NOT THROUGH (N={res_touch['n']}): Wins={res_touch['wins']}, Losses={res_touch['losses']}, WR={res_touch['win_rate']:.2f}%, Avg R={res_touch['avg_r']:.4f}R")


# =========================================================================
# PART B: Pre-registered Market-Entry Variant
# "Entry at the open of the bar after FVG completion, same stop, spread paid"
# =========================================================================
print("\n=== PART B: PRE-REGISTERED MARKET-ENTRY VARIANT ===")

def run_market_entry_silver_bullet(bars, label=""):
    strat, _ = load_silver_bullet_strategy(spec)
    orig_f = strat.on_bar
    def p_f(b: Bar):
        res = orig_f(b)
        idx = strat._detector.bar_count
        if idx > 400:
            strat._detector.drop_levels(idx - 400)
        return res
    strat.on_bar = p_f

    active_t: SimulatedTrade | None = None
    trades: list[SimulatedTrade] = []
    trade_id = 0

    # We track setups to enter at open of NEXT bar
    pending_market_entry = None # (side, sl, tp, orig_limit, setup_bar_idx)

    for idx, bar in enumerate(bars):
        bar_time = bar.timestamp_utc
        ny_time = bar_time.astimezone(NY_TZ)
        mid = bar.mid_ohlc

        # 1. Manage active trade
        if active_t is not None:
            stop_hit = (mid.low <= active_t.stop_loss) if active_t.side == "BUY" else (mid.high >= active_t.stop_loss)
            tp_hit = (mid.high >= active_t.take_profit) if active_t.side == "BUY" else (mid.low <= active_t.take_profit)
            dynamic_close = ny_time.time() >= dt.time(16, 45)

            if stop_hit and tp_hit:
                runner._close_trade(active_t, exit_price=active_t.stop_loss, exit_time=bar_time, reason="STOP_LOSS (conflict)", is_stop=True)
                trades.append(active_t)
                active_t = None
            elif stop_hit:
                runner._close_trade(active_t, exit_price=active_t.stop_loss, exit_time=bar_time, reason="STOP_LOSS", is_stop=True)
                trades.append(active_t)
                active_t = None
            elif tp_hit:
                runner._close_trade(active_t, exit_price=active_t.take_profit, exit_time=bar_time, reason="TAKE_PROFIT", is_stop=False)
                trades.append(active_t)
                active_t = None
            elif dynamic_close:
                runner._close_trade(active_t, exit_price=mid.close, exit_time=bar_time, reason="DYNAMIC_CLOSE_1645", is_stop=False)
                trades.append(active_t)
                active_t = None

        # 2. Execute pending market entry at open of this bar
        if pending_market_entry is not None:
            p_side, p_sl, p_tp = pending_market_entry
            pending_market_entry = None

            if active_t is None and ny_time.time() < dt.time(16, 45):
                trade_id += 1
                entry_price = mid.open
                risk_d = abs(entry_price - p_sl)
                reward_d = abs(p_tp - entry_price)

                active_t = SimulatedTrade(
                    trade_id=f"sb-mkt-{trade_id:04d}",
                    strategy="Silver Bullet (Market Entry)",
                    side=p_side,
                    entry_time=bar_time,
                    entry_price=entry_price,
                    stop_loss=p_sl,
                    take_profit=p_tp,
                    risk_dollars=risk_d,
                    reward_dollars=reward_d,
                    spread_paid=runner.effective_spread,
                    in_sample=True,
                )

        # 3. Step strategy
        intents = strat.on_bar(bar)
        for intent in intents:
            if type(intent).__name__ == "PlaceLimit":
                side_s = "BUY" if intent.side.value == "BUY" else "SELL"
                # Queue market entry for next bar's open
                pending_market_entry = (side_s, intent.stop_price, intent.target_price)

    t_dicts = [_trade_to_dict(t) for t in trades]
    m = compute_trade_metrics(t_dicts, seed=42)
    print(f"\n{label} Market-Entry Silver Bullet:")
    print(f"  N Trades: {m['n_trades']}")
    print(f"  Win Rate: {m['win_rate'] * 100:.2f}% (Wins: {sum(1 for t in t_dicts if t['r_multiple'] > 0)}, Losses: {sum(1 for t in t_dicts if t['r_multiple'] < 0)})")
    print(f"  Expectancy: {m['expectancy']:.4f}R")
    print(f"  95% Bootstrap CI: [{m['ci_95_lower']:.4f}, {m['ci_95_upper']:.4f}]")
    print(f"  Profit Factor: {m['profit_factor']:.2f}")
    return m

m_mkt_20_22 = run_market_entry_silver_bullet(bars_20_22, "2020-2022")

bars_23_25_raw = load_all_m5_bars(years=["2023", "2024", "2025"])
seen_23_25 = set()
bars_23_25 = []
for b in sorted(bars_23_25_raw, key=lambda x: x.timestamp_utc):
    if b.timestamp_utc not in seen_23_25:
        seen_23_25.add(b.timestamp_utc)
        bars_23_25.append(b)
m_mkt_23_25 = run_market_entry_silver_bullet(bars_23_25, "2023-2025")
