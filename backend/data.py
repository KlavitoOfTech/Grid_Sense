"""
data.py - Simulated hourly electricity usage for a Nigerian household.

Generates 60-90 days of hourly data with:
  - realistic daily/weekly demand patterns (morning + evening peaks, weekend bump)
  - grid outages (several short ones per day, plus occasional long ones)
  - generator use during outages (only at certain hours, to save fuel)
  - a few injected anomalies with ground-truth labels (for testing detection)

Run directly to create ../data/usage.csv:
    python data.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CSV_PATH = DATA_DIR / "usage.csv"

# Hours of the day when the household runs the generator during an outage.
# Outside these hours, an outage means the house just goes dark / low load.
GENERATOR_HOURS = set(range(6, 9)) | set(range(17, 23))


def _base_profile(hour: int) -> float:
    """Typical household demand (kWh in one hour) by hour of day."""
    profile = {
        0: 0.20, 1: 0.17, 2: 0.15, 3: 0.15, 4: 0.16, 5: 0.25,
        6: 0.55, 7: 0.80, 8: 0.70, 9: 0.50, 10: 0.40, 11: 0.40,
        12: 0.50, 13: 0.55, 14: 0.50, 15: 0.45, 16: 0.55, 17: 0.80,
        18: 1.10, 19: 1.30, 20: 1.25, 21: 1.00, 22: 0.60, 23: 0.35,
    }
    return profile[hour]


def _simulate_outages(index: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """Return a boolean array: True = grid is available, False = outage."""
    grid_on = np.ones(len(index), dtype=bool)
    n_days = len(index) // 24

    for d in range(n_days):
        day_start = d * 24
        # 1-3 short outages per day, lasting 1-5 hours
        for _ in range(rng.integers(1, 4)):
            start = day_start + rng.integers(0, 24)
            length = rng.integers(1, 6)
            grid_on[start:start + length] = False
        # roughly one long outage every ~10 days (8-20 hours)
        if rng.random() < 0.10:
            start = day_start + rng.integers(0, 12)
            length = rng.integers(8, 21)
            grid_on[start:start + length] = False

    return grid_on


def generate_usage(days: int = 75, seed: int = 42, start: str = "2026-08-01") -> pd.DataFrame:
    """
    Build the simulated dataset.

    Columns:
        timestamp      hourly datetime
        temp_c         simulated outdoor temperature
        grid_on        True if grid supply was available
        demand_kwh     energy the household actually used that hour
        grid_kwh       portion supplied by the grid
        generator_kwh  portion supplied by the generator
        source         'grid' | 'generator' | 'none'
        is_anomaly     ground-truth label (for evaluating detection)
        anomaly_type   short description of the injected anomaly, if any
    """
    rng = np.random.default_rng(seed)
    index = pd.date_range(start=start, periods=days * 24, freq="h")
    n = len(index)

    # --- Temperature: daily cycle + slow drift + noise ---------------------
    hours = np.asarray(index.hour)
    day_num = np.arange(n) // 24
    temp = 29 + 5 * np.sin((hours - 9) / 24 * 2 * np.pi) + 1.5 * np.sin(day_num / 12) + rng.normal(0, 0.8, n)

    # --- Demand ------------------------------------------------------------
    base = np.array([_base_profile(h) for h in hours])
    weekend = np.asarray(index.dayofweek >= 5)
    base = np.where(weekend, base * 1.15, base)          # more home time on weekends

    # Air-con / fan load when it is hot in the afternoon and evening
    hot_extra = np.clip(temp - 31, 0, None) * 0.12
    demand = base + hot_extra + rng.normal(0, 0.06, n)
    demand = np.clip(demand, 0.05, None)

    # --- Grid availability and who supplies the load -----------------------
    grid_on = _simulate_outages(index, rng)
    gen_hour = np.array([h in GENERATOR_HOURS for h in hours])

    grid_kwh = np.zeros(n)
    gen_kwh = np.zeros(n)
    used = demand.copy()
    source = np.empty(n, dtype=object)

    for i in range(n):
        if grid_on[i]:
            grid_kwh[i] = used[i]
            source[i] = "grid"
        elif gen_hour[i]:
            # Generator can only carry a reduced load (~75%) - not the whole house
            used[i] = used[i] * 0.75
            gen_kwh[i] = used[i]
            source[i] = "generator"
        else:
            used[i] = 0.03                                # standby only
            source[i] = "none"

    df = pd.DataFrame({
        "timestamp": index,
        "temp_c": temp.round(1),
        "grid_on": grid_on,
        "demand_kwh": used,
        "grid_kwh": grid_kwh,
        "generator_kwh": gen_kwh,
        "source": source,
        "is_anomaly": False,
        "anomaly_type": "",
    })

    df = _inject_anomalies(df, rng)
    return df.round({"demand_kwh": 3, "grid_kwh": 3, "generator_kwh": 3})


def _inject_anomalies(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Add a handful of clearly unusual events, with ground-truth labels."""
    n = len(df)

    def bump(start: int, length: int, extra: float, label: str):
        end = min(start + length, n)
        idx = df.index[start:end]
        df.loc[idx, "demand_kwh"] += extra
        # Extra load goes to whichever source is active; if none, it's the grid
        for i in idx:
            if df.at[i, "source"] == "generator":
                df.at[i, "generator_kwh"] += extra
            elif df.at[i, "source"] == "grid":
                df.at[i, "grid_kwh"] += extra
            else:
                df.at[i, "source"] = "grid"
                df.at[i, "grid_kwh"] = df.at[i, "demand_kwh"]
        df.loc[idx, "is_anomaly"] = True
        df.loc[idx, "anomaly_type"] = label

    # Place anomalies at fixed fractions of the timeline so they exist for any length
    bump(int(n * 0.15), 4, 2.5, "appliance left on (spike)")
    bump(int(n * 0.30) + 2, 8, 0.9, "night-time standing load (faulty appliance)")
    bump(int(n * 0.50) + 13, 3, 3.2, "heavy load surge")
    bump(int(n * 0.68) + 1, 12, 0.7, "all-day elevated load")
    bump(int(n * 0.85) + 20, 2, 4.0, "extreme spike (possible meter fault)")
    return df


def load_or_create(days: int = 75, force: bool = False) -> pd.DataFrame:
    """Load the CSV if it exists, otherwise generate and save it."""
    if CSV_PATH.exists() and not force:
        return pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df = generate_usage(days=days)
    df.to_csv(CSV_PATH, index=False)
    return df


if __name__ == "__main__":
    df = load_or_create(force=True)
    print(f"Saved {len(df)} hourly rows to {CSV_PATH}")
    print(df.head(10).to_string(index=False))
    print("\nAnomaly hours:", int(df["is_anomaly"].sum()))
    print("Outage hours :", int((~df["grid_on"]).sum()))
