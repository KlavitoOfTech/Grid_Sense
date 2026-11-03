"""
analysis.py - Consumption analysis for GridSense.

Turns hourly usage data into: daily totals, peak-hour analysis, and cost
estimates (grid vs generator).

EDIT THE CONFIG BELOW to match the user's real tariff and fuel price.
The defaults are placeholders - look up your DisCo's current tariff for your
band and the current local fuel price before presenting real figures.
"""

import pandas as pd

# ---------------------------------------------------------------------------
# EDITABLE CONFIG  (currency: Nigerian Naira, NGN)
# ---------------------------------------------------------------------------
CONFIG = {
    "grid_tariff_per_kwh": 209.5,     # NGN per kWh - verify your band's current rate
    "fuel_price_per_litre": 1000.0,   # NGN per litre of petrol - verify locally
    "gen_litres_per_kwh": 0.8,        # litres burned per kWh made (small generator)
    "gen_maintenance_per_kwh": 20.0,  # NGN per kWh for oil, servicing, wear
    "generator_rate_per_kwh": None,   # set per user; overrides the fuel-based estimate
    "peak_hours": (18, 22),           # hours treated as the evening peak window
}


def _cfg(config: dict | None) -> dict:
    """Merge any overrides on top of the defaults."""
    return {**CONFIG, **(config or {})}


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------
def generator_cost_per_kwh(config: dict | None = None) -> float:
    """What one kWh from the generator really costs (fuel + maintenance)."""
    c = _cfg(config)
    if c.get("generator_rate_per_kwh") is not None:
        return float(c["generator_rate_per_kwh"])
    return c["gen_litres_per_kwh"] * c["fuel_price_per_litre"] + c["gen_maintenance_per_kwh"]


def add_costs(df: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """Return a copy of the hourly data with grid, generator and total cost columns."""
    c = _cfg(config)
    out = df.copy()
    out["grid_cost"] = out["grid_kwh"] * c["grid_tariff_per_kwh"]
    out["generator_cost"] = out["generator_kwh"] * generator_cost_per_kwh(c)
    out["total_cost"] = out["grid_cost"] + out["generator_cost"]
    return out


# ---------------------------------------------------------------------------
# Daily totals
# ---------------------------------------------------------------------------
def daily_totals(df: pd.DataFrame, config: dict | None = None) -> pd.DataFrame:
    """One row per day: energy by source, outage hours, and cost."""
    d = add_costs(df, config)
    d["date"] = d["timestamp"].dt.date
    d["outage_hour"] = (~d["grid_on"]).astype(int)

    daily = d.groupby("date").agg(
        total_kwh=("demand_kwh", "sum"),
        grid_kwh=("grid_kwh", "sum"),
        generator_kwh=("generator_kwh", "sum"),
        outage_hours=("outage_hour", "sum"),
        grid_cost=("grid_cost", "sum"),
        generator_cost=("generator_cost", "sum"),
        total_cost=("total_cost", "sum"),
        peak_kwh=("demand_kwh", "max"),
    ).reset_index()

    daily["date"] = daily["date"].astype(str)
    return daily.round(2)


# ---------------------------------------------------------------------------
# Peaks and patterns
# ---------------------------------------------------------------------------
def hourly_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Average demand for each hour of the day (0-23)."""
    profile = df.groupby(df["timestamp"].dt.hour)["demand_kwh"].mean().reset_index()
    profile.columns = ["hour", "avg_kwh"]
    return profile.round(3)


def peak_analysis(df: pd.DataFrame, config: dict | None = None) -> dict:
    """Peak hours, peak day, and how 'peaky' the load is."""
    c = _cfg(config)
    profile = hourly_profile(df)
    top_hours = profile.nlargest(3, "avg_kwh")

    daily_kwh = df.groupby(df["timestamp"].dt.date)["demand_kwh"].sum()
    peak_day = daily_kwh.idxmax()

    hourly_max = df.loc[df["demand_kwh"].idxmax()]
    avg_load = df["demand_kwh"].mean()

    lo, hi = c["peak_hours"]
    in_window = df["timestamp"].dt.hour.between(lo, hi)
    peak_window_share = df.loc[in_window, "demand_kwh"].sum() / df["demand_kwh"].sum()

    weekday = df[df["timestamp"].dt.dayofweek < 5]["demand_kwh"].sum() / max(
        1, df[df["timestamp"].dt.dayofweek < 5]["timestamp"].dt.date.nunique())
    weekend = df[df["timestamp"].dt.dayofweek >= 5]["demand_kwh"].sum() / max(
        1, df[df["timestamp"].dt.dayofweek >= 5]["timestamp"].dt.date.nunique())

    return {
        "top_peak_hours": [
            {"hour": int(r.hour), "avg_kwh": float(r.avg_kwh)} for r in top_hours.itertuples()
        ],
        "peak_day": str(peak_day),
        "peak_day_kwh": round(float(daily_kwh.max()), 2),
        "highest_single_hour": {
            "timestamp": str(hourly_max["timestamp"]),
            "kwh": round(float(hourly_max["demand_kwh"]), 2),
        },
        "peak_window": f"{lo}:00-{hi}:59",
        "peak_window_share_pct": round(float(peak_window_share) * 100, 1),
        "load_factor": round(float(avg_load / df["demand_kwh"].max()), 3),
        "avg_weekday_kwh": round(float(weekday), 2),
        "avg_weekend_kwh": round(float(weekend), 2),
    }


# ---------------------------------------------------------------------------
# Cost summary
# ---------------------------------------------------------------------------
def cost_summary(df: pd.DataFrame, config: dict | None = None) -> dict:
    """Overall cost picture: grid vs generator, and what switching could save."""
    c = _cfg(config)
    d = add_costs(df, c)
    n_days = max(1, d["timestamp"].dt.date.nunique())

    grid_kwh = float(d["grid_kwh"].sum())
    gen_kwh = float(d["generator_kwh"].sum())
    grid_cost = float(d["grid_cost"].sum())
    gen_cost = float(d["generator_cost"].sum())
    total_cost = grid_cost + gen_cost
    gen_rate = generator_cost_per_kwh(c)

    # What the generator energy would have cost if it came from the grid
    saving_if_grid = gen_kwh * (gen_rate - c["grid_tariff_per_kwh"])

    return {
        "currency": c.get("currency", "NGN"),
        "days": n_days,
        "total_kwh": round(grid_kwh + gen_kwh, 1),
        "grid_kwh": round(grid_kwh, 1),
        "generator_kwh": round(gen_kwh, 1),
        "generator_share_pct": round(gen_kwh / max(1e-9, grid_kwh + gen_kwh) * 100, 1),
        "grid_cost": round(grid_cost),
        "generator_cost": round(gen_cost),
        "total_cost": round(total_cost),
        "avg_daily_cost": round(total_cost / n_days),
        "projected_30_day_cost": round(total_cost / n_days * 30),
        "grid_rate_per_kwh": c["grid_tariff_per_kwh"],
        "generator_rate_per_kwh": round(gen_rate, 1),
        "generator_to_grid_cost_ratio": round(gen_rate / c["grid_tariff_per_kwh"], 2),
        "potential_saving_if_grid_available": round(saving_if_grid),
        "outage_hours": int((~d["grid_on"]).sum()),
        "outage_pct": round(float((~d["grid_on"]).mean()) * 100, 1),
    }


# ---------------------------------------------------------------------------
# Compact summary for the AI advisor (used later)
# ---------------------------------------------------------------------------
def build_summary(df: pd.DataFrame, config: dict | None = None) -> dict:
    """A small JSON-friendly dict the Meta model can reason over."""
    return {
        "costs": cost_summary(df, config),
        "peaks": peak_analysis(df, config),
    }

# ---------------------------------------------------------------------------
# Simple daily baseline estimate (Phase 7)
# ---------------------------------------------------------------------------
def daily_baseline(df: pd.DataFrame, config: dict | None = None, window: int = 7) -> dict:
    """Average of the most recent complete days, projected to a day and a 30-day month."""
    c = _cfg(config)
    d = df.copy()
    d["date"] = d["timestamp"].dt.date
    g = d.groupby("date").agg(
        hours=("demand_kwh", "size"),
        total_kwh=("demand_kwh", "sum"),
        grid_kwh=("grid_kwh", "sum"),
        generator_kwh=("generator_kwh", "sum"),
    )
    full = g[g["hours"] >= 24]  # skip partial days, they would drag the average down
    if len(full) < 3:
        raise ValueError("Need at least 3 complete days of readings for an estimate.")

    recent = full.tail(window)
    daily_kwh = float(recent["total_kwh"].mean())
    grid_day = float(recent["grid_kwh"].mean())
    gen_day = float(recent["generator_kwh"].mean())
    tariff = float(c["grid_tariff_per_kwh"])
    gen_rate = generator_cost_per_kwh(c)

    trend = None
    if len(full) >= 2 * window:
        prev = float(full.iloc[-2 * window:-window]["total_kwh"].mean())
        if prev > 0:
            trend = round((daily_kwh - prev) / prev * 100, 1)

    monthly_kwh = daily_kwh * 30
    supplied = max(1e-9, grid_day + gen_day)
    return {
        "currency": c.get("currency", "NGN"),
        "days_used": int(len(recent)),
        "window_start": str(recent.index[0]),
        "window_end": str(recent.index[-1]),
        "predicted_daily_kwh": round(daily_kwh, 2),
        "predicted_monthly_kwh": round(monthly_kwh, 1),
        "daily_kwh_min": round(float(recent["total_kwh"].min()), 2),
        "daily_kwh_max": round(float(recent["total_kwh"].max()), 2),
        "trend_vs_previous_window_pct": trend,
        "grid_tariff_per_kwh": tariff,
        "generator_rate_per_kwh": round(gen_rate, 1),
        "grid_share_pct": round(grid_day / supplied * 100, 1),
        "generator_share_pct": round(gen_day / supplied * 100, 1),
        "monthly_cost_all_grid": round(monthly_kwh * tariff),
        "monthly_cost_expected_mix": round((grid_day * tariff + gen_day * gen_rate) * 30),
        "method": f"Average of your last {len(recent)} complete days, projected over 30 days. "
                  "A simple baseline, not a trained model.",
    }
    
if __name__ == "__main__":
    from data import load_or_create

    data = load_or_create()
    print(daily_totals(data).head())
    print()
    print("COSTS:", cost_summary(data))
    print()
    print("PEAKS:", peak_analysis(data))
