"""
store.py - Load and save ONE user's data.

Every database call here uses the user's own access token, so Supabase Row Level
Security applies on top of our checks: even a bug in this file could not return
another user's rows. Per-user state is always keyed by the verified user id.
"""

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import timedelta, timezone

import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import HTTPException

from data import DATA_DIR, generate_usage

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
LAGOS = timezone(timedelta(hours=1))  # Nigeria has no daylight saving
PAGE = 1000                           # Supabase returns at most 1000 rows per request
CONTEXT_SECONDS = 30
ENTRY_DIR = DATA_DIR / "entries"


@dataclass
class Ctx:
    """Everything the analysis and the AI agent may see: one user's data only."""
    user_id: str
    df: pd.DataFrame
    config: dict


# ---------------------------------------------------------------------------
# Supabase REST helper
# ---------------------------------------------------------------------------
def _rest(method: str, path: str, token: str, headers: dict | None = None, **kwargs):
    base = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}", **(headers or {})}
    try:
        r = requests.request(method, f"{SUPABASE_URL}/rest/v1/{path}", headers=base, timeout=20, **kwargs)
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if r.status_code >= 400:
        print(f"[store] {method} {path} -> {r.status_code} {r.text[:200]}")
        raise HTTPException(status_code=502, detail="Database error")
    return r


def rows_to_df(rows: list) -> pd.DataFrame:
    """Turn stored readings into the columns the analysis code expects."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(LAGOS).dt.tz_localize(None)
    df["demand_kwh"] = df["demand_kwh"].astype(float)
    df["grid_kwh"] = df["demand_kwh"].where(df["source"] == "grid", 0.0)
    df["generator_kwh"] = df["demand_kwh"].where(df["source"] == "generator", 0.0)
    df["grid_on"] = df["source"] == "grid"
    return df.sort_values("timestamp").reset_index(drop=True)


def fetch_readings(token: str) -> pd.DataFrame:
    rows, offset = [], 0
    while True:
        batch = _rest("GET", f"energy_readings?select=timestamp,demand_kwh,source"
                             f"&order=timestamp.asc&limit={PAGE}&offset={offset}", token).json()
        rows += batch
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows_to_df(rows)


def fetch_settings(token: str) -> dict:
    rows = _rest("GET", "energy_settings?select=grid_tariff,generator_rate&limit=1", token).json()
    if not rows:
        return {}
    return {"grid_tariff_per_kwh": float(rows[0]["grid_tariff"]),
            "generator_rate_per_kwh": float(rows[0]["generator_rate"])}


# ---------------------------------------------------------------------------
# Per-user context (short cache so one dashboard load doesn't re-query 8 times)
# ---------------------------------------------------------------------------
_ctx_cache: dict = {}


def get_context(user) -> Ctx:
    hit = _ctx_cache.get(user.id)
    if hit and hit[0] > time.time():
        return hit[1]
    df = fetch_readings(user.token)
    if df.empty:
        raise HTTPException(status_code=409, detail="no_data")
    ctx = Ctx(user_id=user.id, df=df, config=fetch_settings(user.token))
    _ctx_cache[user.id] = (time.time() + CONTEXT_SECONDS, ctx)
    return ctx


def invalidate(user_id: str) -> None:
    _ctx_cache.pop(user_id, None)


# ---------------------------------------------------------------------------
# Demo data for a new user (stored in THEIR account, different for each user)
# ---------------------------------------------------------------------------
def seed_demo(user, days: int = 75) -> int:
    if _rest("GET", "energy_readings?select=id&limit=1", user.token).json():
        return 0  # already has data; never mix demo data into real readings
    cfg = fetch_settings(user.token)
    grid = cfg.get("grid_tariff_per_kwh", 209.5)
    gen = cfg.get("generator_rate_per_kwh", 820.0)
    seed = int(user.id.replace("-", ""), 16) % (2 ** 32)
    start = (pd.Timestamp.now(tz=LAGOS).normalize().tz_localize(None) - pd.Timedelta(days=days)).strftime("%Y-%m-%d")

    df = generate_usage(days=days, seed=seed, start=start)
    rows = [
        {"timestamp": t.isoformat() + "+01:00", "demand_kwh": float(d), "source": s,
         "cost": round(float(g * grid + e * gen), 2)}
        for t, d, s, g, e in zip(df["timestamp"], df["demand_kwh"], df["source"], df["grid_kwh"], df["generator_kwh"])
    ]
    for i in range(0, len(rows), 500):
        _rest("POST", "energy_readings?on_conflict=user_id,timestamp", user.token,
              headers={"Prefer": "resolution=ignore-duplicates,return=minimal"}, json=rows[i:i + 500])
    invalidate(user.id)
    return len(rows)


# ---------------------------------------------------------------------------
# Saving AI output to the user's own history (best effort: never breaks a request)
# ---------------------------------------------------------------------------
def _save(table: str, row: dict, token: str) -> None:
    try:
        _rest("POST", table, token, headers={"Prefer": "return=minimal"}, json=row)
    except Exception as exc:
        print(f"[store] could not save to {table}: {exc}")


def save_chat(user, question: str, answer: str) -> None:
    _save("chat_history", {"question": question, "answer": answer}, user.token)


def save_plan(user, plan: dict) -> None:
    _save("savings_plans", {"plan": plan}, user.token)


# ---------------------------------------------------------------------------
# Logged readings from photos: one file per verified user id
# ---------------------------------------------------------------------------
def _entry_path(user_id: str):
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", user_id):
        raise ValueError("bad user id")
    return ENTRY_DIR / f"{user_id}.json"


def list_entries(user_id: str) -> list:
    path = _entry_path(user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def add_entry(user_id: str, entry: dict) -> dict:
    from datetime import datetime

    entries = list_entries(user_id)
    entry = {**entry, "logged_at": datetime.now().isoformat(timespec="seconds")}
    entries.append(entry)
    ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    _entry_path(user_id).write_text(json.dumps(entries, indent=2))
    return entry
