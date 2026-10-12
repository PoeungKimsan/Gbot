"""Stats script for forward trading logs (forward_log.csv).

Pre-registration: research/prereg/trend_v1.2.md.
Requirement 4: Counts an OPEN row and its later exit row as ONE single trade.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from forward.forward_log import LOG_CSV_PATH, compute_forward_stats


def print_stats_report(csv_path: Path = LOG_CSV_PATH) -> dict[str, Any]:
    stats = compute_forward_stats(csv_path=csv_path)
    print("=" * 60)
    print("FORWARD LOG PERFORMANCE SUMMARY (Requirement 4: OPEN + exit = 1 trade):")
    print(f"  Total Trades: {stats['total_trades']} [FLAG: N < 200, N={stats['total_trades']}]")
    print(f"  Open Trades: {stats['open_trades']}")
    print(f"  Closed Trades: {stats['closed_trades']}")
    if stats["closed_trades"] > 0:
        print(f"  Win Rate: {stats['win_rate']:.2f}% ({stats['wins']}/{stats['closed_trades']})")
        print(f"  Mean Net R: {stats['mean_net_r']:+.4f}R")
        print(f"  Cum Net R: {stats['cum_net_r']:+.4f}R")
        print(f"  Max Drawdown: {stats['max_dd']:.4f}R")
    else:
        print("  Win Rate: N/A (0 closed trades)")
        print("  Mean Net R: 0.0000R")
        print("  Cum Net R: 0.0000R")
        print("  Max Drawdown: 0.0000R")
    print("=" * 60)
    return stats


if __name__ == "__main__":
    csv_file = Path(sys.argv[1]) if len(sys.argv) > 1 else LOG_CSV_PATH
    print_stats_report(csv_file)
