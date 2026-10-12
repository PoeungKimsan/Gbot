"""Closing task: Leverage audit at $2,000 and $4,100 gold.

Computes lots, notional, leverage, and effective risk % when the 10-lot clamp binds.
"""

from decimal import Decimal
import json
from pathlib import Path

def compute_leverage_audit() -> dict:
    equities = [Decimal("10000"), Decimal("50000"), Decimal("100000")]
    stops = [Decimal("0.21"), Decimal("0.30"), Decimal("0.50"), Decimal("1.00")]
    gold_prices = [Decimal("2000"), Decimal("4100")]
    contract_size = Decimal("100") # 100 oz per lot
    max_lot_clamp = Decimal("10.00")
    risk_pct = Decimal("0.01") # 1%

    results = []

    for eq in equities:
        for s in stops:
            risk_budget = eq * risk_pct
            dollar_risk_per_lot = s * contract_size
            raw_lots = risk_budget / dollar_risk_per_lot
            # Floor to 2 decimals as done in live engine floor_to_step
            raw_lots_floored = (raw_lots * 100).to_integral_value(rounding="ROUND_FLOOR") / Decimal("100")
            clamped_lots = min(raw_lots_floored, max_lot_clamp)
            clamp_binds = raw_lots_floored > max_lot_clamp

            # Effective risk
            eff_risk_dollars = clamped_lots * contract_size * s
            eff_risk_pct = (eff_risk_dollars / eq) * Decimal("100")

            # Notional & leverage at 2000
            notional_2k = clamped_lots * contract_size * Decimal("2000")
            lev_2k = notional_2k / eq

            # Notional & leverage at 4100
            notional_4k = clamped_lots * contract_size * Decimal("4100")
            lev_4k = notional_4k / eq

            row = {
                "equity": int(eq),
                "stop_distance": float(s),
                "raw_lots": float(raw_lots_floored),
                "clamped_lots": float(clamped_lots),
                "clamp_binds": clamp_binds,
                "eff_risk_pct": float(eff_risk_pct),
                "eff_risk_dollars": float(eff_risk_dollars),
                "at_2000": {
                    "gold_price": 2000,
                    "notional_usd": float(notional_2k),
                    "leverage": float(lev_2k),
                },
                "at_4100": {
                    "gold_price": 4100,
                    "notional_usd": float(notional_4k),
                    "leverage": float(lev_4k),
                },
            }
            results.append(row)

    out_file = Path("research/output/closing_leverage_table.json")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print("Saved closing_leverage_table.json successfully.\n")

    header = "| Equity | Stop | Raw Lots | Lots (Clamped) | Clamp Binds? | Notional (@$2,000) | Lev (@$2,000) | Notional (@$4,100) | Lev (@$4,100) | Effective Risk % |"
    sep = "|---|---|---|---|---|---|---|---|---|---|"
    print(header)
    print(sep)
    for r in results:
        eq_s = f"${r['equity']:,}"
        stp_s = f"${r['stop_distance']:.2f}"
        raw_s = f"{r['raw_lots']:.2f}"
        clp_s = f"{r['clamped_lots']:.2f}"
        bnd_s = str(r['clamp_binds'])
        not2_s = f"${r['at_2000']['notional_usd']:,.0f}"
        lev2_s = f"{r['at_2000']['leverage']:.1f}x"
        not4_s = f"${r['at_4100']['notional_usd']:,.0f}"
        lev4_s = f"{r['at_4100']['leverage']:.1f}x"
        eff_s = f"{r['eff_risk_pct']:.2f}%"
        print(f"| {eq_s:<8} | {stp_s:<6} | {raw_s:>8} | {clp_s:>14} | {bnd_s:<12} | {not2_s:>18} | {lev2_s:>13} | {not4_s:>18} | {lev4_s:>13} | {eff_s:>16} |")

    return {"rows": results}

if __name__ == "__main__":
    compute_leverage_audit()
