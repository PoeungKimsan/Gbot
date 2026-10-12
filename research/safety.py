"""Safety controls for the research harness.

Enforces:
1. Low process priority so running research jobs never starves the live daemon.
2. America/New_York live execution window guards (02:00-05:00 and 10:00-11:00 NY).
"""

from __future__ import annotations

import ctypes
import datetime as dt
import logging
import os
import sys
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

NY_TZ = ZoneInfo("America/New_York")

# Win32 priority classes
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
IDLE_PRIORITY_CLASS = 0x00000040


def set_low_process_priority() -> bool:
    """Set current process to below-normal/idle priority on Windows and Unix."""
    if sys.platform == "win32":
        try:
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            handle = k32.GetCurrentProcess()
            # Set to below normal priority
            res = k32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
            if res != 0:
                logger.info("Process priority set to BELOW_NORMAL_PRIORITY_CLASS")
                return True
            logger.warning("Failed to set process priority, Win32 error: %s", k32.GetLastError())
            return False
        except Exception as exc:
            logger.warning("Error adjusting process priority: %s", exc)
            return False
    else:
        try:
            os.nice(10)
            logger.info("Unix process nice value increased by 10")
            return True
        except Exception as exc:
            logger.warning("Error adjusting unix nice value: %s", exc)
            return False


def is_live_trading_window(now: dt.datetime | None = None) -> bool:
    """Check if current New York time falls inside protected live bot trading windows.

    Protected windows:
    - 02:00 - 05:00 America/New_York (London Open / Judas Swing)
    - 10:00 - 11:00 America/New_York (New York AM / Silver Bullet)

    During market hours (Sunday 17:00 NY through Friday 17:00 NY), heavy jobs
    must yield or wait.
    """
    if now is None:
        now = dt.datetime.now(NY_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=NY_TZ)
    else:
        now = now.astimezone(NY_TZ)

    current_time = now.time()
    weekday = now.weekday()  # Monday is 0, Sunday is 6

    # Forex market hours check
    # Forex is closed from Friday 17:00 NY to Sunday 17:00 NY
    is_weekend_closed = False
    if weekday == 4 and current_time >= dt.time(17, 0):  # Friday after 17:00 NY
        is_weekend_closed = True
    elif weekday == 5:  # Saturday all day
        is_weekend_closed = True
    elif weekday == 6 and current_time < dt.time(17, 0):  # Sunday before 17:00 NY
        is_weekend_closed = True

    # Check the prohibited windows
    in_london_window = dt.time(2, 0) <= current_time < dt.time(5, 0)
    in_ny_am_window = dt.time(10, 0) <= current_time < dt.time(11, 0)

    is_window = in_london_window or in_ny_am_window
    return is_window and not is_weekend_closed


def verify_execution_safety(force: bool = False) -> None:
    """Ensure process priority is lowered and time window safety is satisfied."""
    set_low_process_priority()

    if is_live_trading_window():
        msg = (
            "SAFETY VIOLATION: Current time is within live trading windows "
            "(02:00-05:00 or 10:00-11:00 America/New_York) while markets are open. "
            "Heavy research jobs must not run at this time to protect live execution."
        )
        if not force:
            raise RuntimeError(msg)
        logger.warning("%s (Bypassed with force=True)", msg)
