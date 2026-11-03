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
from datetime import datetime, timedelta, timezone


import pandas as pd
import requests
from dotenv import load_dotenv
from fastapi import HTTPException

import analysis
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
    token: str = ""


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


SETTINGS_DEFAULTS = {
    "currency": "NGN",
    "grid_tariff": 209.5,
    "generator_cost_mode": "per_kwh",
    "generator_rate": 820.0,
    "fuel_price_per_litre": 1000.0,
    "gen_litres_per_kwh": 0.8,
}
_SETTINGS_COLS = "currency,grid_tariff,generator_cost_mode,generator_rate,fuel_price_per_litre,gen_litres_per_kwh"


def fetch_settings_row(token: str) -> dict | None:
    """The user's raw energy_settings row (RLS returns only their own), or None."""
    rows = _rest("GET", f"energy_settings?select={_SETTINGS_COLS}&limit=1", token).json()
    return rows[0] if rows else None


def fetch_settings(token: str) -> dict:
    """The user's settings as an analysis config. Empty if they have none yet."""
    row = fetch_settings_row(token)
    if not row:
        return {}
    cfg = {"currency": row["currency"], "grid_tariff_per_kwh": float(row["grid_tariff"])}
    if row["generator_cost_mode"] == "fuel":
        cfg["fuel_price_per_litre"] = float(row["fuel_price_per_litre"])
        cfg["gen_litres_per_kwh"] = float(row["gen_litres_per_kwh"])
    else:
        cfg["generator_rate_per_kwh"] = float(row["generator_rate"])
    return cfg

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
    ctx = Ctx(user_id=user.id, df=df, config=fetch_settings(user.token), token=user.token)
    _ctx_cache[user.id] = (time.time() + CONTEXT_SECONDS, ctx)
    return ctx


def invalidate(user_id: str) -> None:
    _ctx_cache.pop(user_id, None)


# ---------------------------------------------------------------------------
# Onboarding and settings (profiles + energy_settings)
# ---------------------------------------------------------------------------
def get_settings_payload(user) -> dict:
    """What the settings form needs. Missing values fall back to defaults so the form is pre-filled."""
    prof = _rest("GET", f"profiles?select=account_name,user_type,city,state,onboarding_completed"
                        f"&id=eq.{user.id}&limit=1", user.token).json()
    prof = prof[0] if prof else {}
    row = fetch_settings_row(user.token) or {}
    e = {**SETTINGS_DEFAULTS, **{k: v for k, v in row.items() if v is not None}}
    return {
        "onboarding_completed": bool(prof.get("onboarding_completed")),
        "profile": {
            "account_name": prof.get("account_name") or "",
            "user_type": prof.get("user_type") or "household",
            "city": prof.get("city") or "",
            "state": prof.get("state") or "",
        },
        "energy": {
            "currency": e["currency"],
            "grid_tariff": float(e["grid_tariff"]),
            "generator_cost_mode": e["generator_cost_mode"],
            "generator_rate": float(e["generator_rate"]),
            "fuel_price_per_litre": float(e["fuel_price_per_litre"]),
            "gen_litres_per_kwh": float(e["gen_litres_per_kwh"]),
        },
    }


def save_settings(user, profile: dict, energy: dict) -> dict:
    """Save both tables with the user's own token (RLS applies). Settings go first, so
    onboarding is only marked complete if they were saved."""
    fuel = energy["generator_cost_mode"] == "fuel"
    _rest("POST", "energy_settings?on_conflict=user_id", user.token,
          headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
          json={
              "user_id": user.id,
              "currency": energy["currency"],
              "grid_tariff": energy["grid_tariff"],
              "generator_cost_mode": energy["generator_cost_mode"],
              "generator_rate": None if fuel else energy["generator_rate"],
              "fuel_price_per_litre": energy["fuel_price_per_litre"] if fuel else None,
              "gen_litres_per_kwh": energy["gen_litres_per_kwh"] if fuel else None,
              "updated_at": datetime.now(timezone.utc).isoformat(),
          })
    row = {"id": user.id, **profile, "onboarding_completed": True}
    if user.email:
        row["email"] = user.email
    _rest("POST", "profiles?on_conflict=id", user.token,
          headers={"Prefer": "resolution=merge-duplicates,return=minimal"}, json=row)
    invalidate(user.id)  # so the dashboard uses the new numbers immediately
    return get_settings_payload(user)

# ---------------------------------------------------------------------------
# Demo data for a new user (stored in THEIR account, different for each user)
# ---------------------------------------------------------------------------
def seed_demo(user, days: int = 75) -> int:
    if _rest("GET", "energy_readings?select=id&limit=1", user.token).json():
        return 0  # already has data; never mix demo data into real readings
    cfg = fetch_settings(user.token)
    grid = cfg.get("grid_tariff_per_kwh", 209.5)
    gen = analysis.generator_cost_per_kwh(cfg)
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


def save_chat(user, question: str, answer: str, tools_used=None, mode=None) -> None:
    _save("chat_history", {"user_id": user.id, "question": question, "answer": answer,
                           "tools_used": tools_used or [], "mode": mode}, user.token)

def save_plan(user, plan: dict) -> None:
    _save("savings_plans", {"plan": plan}, user.token)


# ---------------------------------------------------------------------------
# Logged readings from photos (Supabase table logged_readings, RLS applies)
# ---------------------------------------------------------------------------
LOGGED_COLS = ("id,document_type,reading_date,kwh,amount_ngn,litres,price_per_litre_ngn,"
               "meter_reading_kwh,appliance_watts,summary,confidence,image_path,created_at")


def list_entries(token: str, limit: int = 200) -> list:
    """The user's logged readings, newest first (RLS returns only their own)."""
    limit = max(1, min(int(limit), 500))
    return _rest("GET", f"logged_readings?select={LOGGED_COLS}"
                        f"&order=created_at.desc&limit={limit}", token).json()


def add_entry(user, entry: dict) -> dict:
    path = entry.get("image_path")
    if path and (not path.startswith(f"{user.id}/") or ".." in path):
        raise HTTPException(status_code=422, detail="Invalid image path")
    row = {
        "user_id": user.id,
        "document_type": entry["document_type"],
        "reading_date": entry.get("date"),
        "kwh": entry.get("kwh"),
        "amount_ngn": entry.get("amount_ngn"),
        "litres": entry.get("litres"),
        "price_per_litre_ngn": entry.get("price_per_litre_ngn"),
        "meter_reading_kwh": entry.get("meter_reading_kwh"),
        "appliance_watts": entry.get("appliance_watts"),
        "summary": entry.get("summary") or "",
        "confidence": entry.get("confidence"),
        "image_path": path,
    }
    r = _rest("POST", "logged_readings", user.token,
              headers={"Prefer": "return=representation"}, json=row)
    return r.json()[0]


# ---------------------------------------------------------------------------
# Persistent AI results (analysis_runs, energy_alerts, savings_plans, chat_history)
# ---------------------------------------------------------------------------
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}
_ALERT_COLS = "id,title,description,severity,likely_cause,action,created_at"


def data_version(ctx: Ctx) -> str:
    """Changes whenever the user's readings change; saved AI output is 'stale' when it differs."""
    logged = _rest("GET", "logged_readings?select=id&limit=1000", ctx.token).json()
    return f"{len(ctx.df)}|{ctx.df['timestamp'].max()}|{len(logged)}"


def _json_safe(obj):
    return json.loads(json.dumps(obj, default=str))


def _create_run(user, ctx: Ctx, kind: str, tools_used, mode: str) -> dict:
    row = {
        "user_id": user.id, "kind": kind, "data_version": data_version(ctx),
        "summary": _json_safe(analysis.build_summary(ctx.df, ctx.config)),
        "tools_used": tools_used or [], "mode": mode,
    }
    return _rest("POST", "analysis_runs", user.token,
                 headers={"Prefer": "return=representation"}, json=row).json()[0]


def _delete_run(user, run_id: str) -> None:
    try:
        _rest("DELETE", f"analysis_runs?id=eq.{run_id}", user.token)
    except Exception as exc:
        print(f"[store] could not clean up run {run_id}: {exc}")


def _alert_out(a: dict) -> dict:
    a = dict(a)
    a["what_happened"] = a.pop("description", None)  # the field name the dashboard already uses
    return a


def save_alert_run(user, ctx: Ctx, result: dict) -> None:
    run = _create_run(user, ctx, "alerts", result.get("tools_used"), result["mode"])
    rows = [{"user_id": user.id, "run_id": run["id"], "title": a["title"],
             "description": a["what_happened"], "severity": a["severity"],
             "likely_cause": a["likely_cause"], "action": a["action"]}
            for a in result["alerts"]]
    try:
        if rows:
            _rest("POST", "energy_alerts", user.token,
                  headers={"Prefer": "return=minimal"}, json=rows)
    except Exception:
        _delete_run(user, run["id"])  # don't leave a "fresh" run with no alerts
        raise


def get_alerts_payload(user, ctx: Ctx) -> dict:
    runs = _rest("GET", "analysis_runs?select=id,data_version,tools_used,mode,created_at"
                        "&kind=eq.alerts&order=created_at.desc&limit=1", user.token).json()
    if not runs:
        return {"alerts": [], "mode": None, "tools_used": [], "created_at": None, "stale": True}
    run = runs[0]
    rows = _rest("GET", f"energy_alerts?select={_ALERT_COLS}&run_id=eq.{run['id']}&limit=50",
                 user.token).json()
    rows.sort(key=lambda a: SEVERITY_ORDER.get(a["severity"], 3))
    return {"alerts": [_alert_out(a) for a in rows], "mode": run["mode"],
            "tools_used": run["tools_used"], "created_at": run["created_at"],
            "stale": run["data_version"] != data_version(ctx)}


def alert_history(token: str, limit: int = 100) -> list:
    limit = max(1, min(int(limit), 300))
    rows = _rest("GET", f"energy_alerts?select={_ALERT_COLS}&order=created_at.desc&limit={limit}",
                 token).json()
    return [_alert_out(a) for a in rows]


def save_plan_run(user, ctx: Ctx, result: dict) -> None:
    run = _create_run(user, ctx, "plan", result.get("tools_used"), result["mode"])
    try:
        _rest("POST", "savings_plans", user.token, headers={"Prefer": "return=minimal"},
              json={"user_id": user.id, "plan": _json_safe(result["plan"]), "run_id": run["id"]})
    except Exception:
        _delete_run(user, run["id"])
        raise


def get_plan_payload(user, ctx: Ctx) -> dict:
    rows = _rest("GET", "savings_plans?select=id,plan,created_at,analysis_runs(data_version,tools_used,mode)"
                        "&order=created_at.desc&limit=1", user.token).json()
    if not rows:
        return {"plan": None, "mode": None, "tools_used": [], "created_at": None, "stale": False}
    r = rows[0]
    run = r.get("analysis_runs") or {}  # plans saved before Phase 6 have no run: shown as stale
    return {"plan": r["plan"], "mode": run.get("mode", "live"), "tools_used": run.get("tools_used") or [],
            "created_at": r["created_at"], "stale": run.get("data_version") != data_version(ctx)}


def plan_history(token: str, limit: int = 20) -> list:
    limit = max(1, min(int(limit), 100))
    return _rest("GET", f"savings_plans?select=id,plan,created_at&order=created_at.desc&limit={limit}",
                 token).json()


def chat_history(token: str, limit: int = 100) -> list:
    limit = max(1, min(int(limit), 300))
    return _rest("GET", f"chat_history?select=id,question,answer,tools_used,mode,created_at"
                        f"&order=created_at.desc&limit={limit}", token).json()


def analysis_history(token: str, limit: int = 20) -> list:
    limit = max(1, min(int(limit), 100))
    return _rest("GET", f"analysis_runs?select=id,kind,created_at,mode,tools_used,summary"
                        f"&order=created_at.desc&limit={limit}", token).json()