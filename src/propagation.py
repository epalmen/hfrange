"""
Basic HF propagation helpers.

Skip zone estimation based on frequency and time of day.
No external API needed — this is a rough model, not a full ray-tracer.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def estimate_skip_zone_km(freq_mhz: float, dt: datetime | None = None) -> tuple[float, float]:
    """
    Return (min_skip_km, max_skip_km) — the dead zone around the transmitter
    where the ionospheric reflected signal does NOT arrive.

    This is a simplified model:
    - Lower frequencies (80m/40m) have smaller skip zones during the day.
    - Higher frequencies (20m/15m/10m) have larger skip zones.
    - Night-time ionosphere is lower/weaker, skip zone shrinks or propagation fails.

    Returns conservative estimates. For accurate prediction use VOACAP or ITURHFPROP.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)

    hour_utc = dt.hour

    # Day vs night flag (very rough)
    is_day = 6 <= hour_utc <= 20

    if freq_mhz < 4:       # 80m / 160m
        min_skip = 100 if is_day else 50
        max_skip = 500 if is_day else 300
    elif freq_mhz < 8:     # 40m
        min_skip = 200 if is_day else 100
        max_skip = 800 if is_day else 500
    elif freq_mhz < 15:    # 20m / 17m
        min_skip = 500 if is_day else 300
        max_skip = 2000 if is_day else 1500
    elif freq_mhz < 22:    # 15m
        min_skip = 800 if is_day else 500
        max_skip = 3000 if is_day else 2000
    else:                  # 10m and above
        min_skip = 1000 if is_day else 0
        max_skip = 4000 if is_day else 1000

    return min_skip, max_skip


def best_scan_range(freq_mhz: float, dt: datetime | None = None) -> tuple[float, float]:
    """
    Return (start_km, end_km) as a practical scan window for KiwiSDR receivers.
    Adds a 20% margin beyond the estimated skip zone.
    """
    min_skip, max_skip = estimate_skip_zone_km(freq_mhz, dt)
    start = max(min_skip * 0.8, 50)   # don't go below 50 km
    end = min(max_skip * 3, 20000)    # up to 3× the max skip distance
    return start, end


def mhz_to_band(freq_mhz: float) -> str:
    """Return amateur band name for a frequency."""
    bands = [
        (1.8, 2.0, "160m"),
        (3.5, 4.0, "80m"),
        (7.0, 7.3, "40m"),
        (10.1, 10.15, "30m"),
        (14.0, 14.35, "20m"),
        (18.068, 18.168, "17m"),
        (21.0, 21.45, "15m"),
        (24.89, 24.99, "12m"),
        (28.0, 29.7, "10m"),
    ]
    for low, high, name in bands:
        if low <= freq_mhz <= high:
            return name
    return f"{freq_mhz:.3f} MHz"
