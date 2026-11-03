
"""
main.py - GridSense API.

Every endpoint except "/" requires a logged-in user.

Run:
    uvicorn main:app --reload

Auth:
    Send Authorization: Bearer <Supabase access token>

Identity comes only from the verified token (see auth.py), never from a user ID
supplied by the client. Each request loads that user's own readings and runs
the analysis and AI agent against them.
"""

import pandas as pd

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

import advisor
import analysis
import forecast
import store

from auth import User, get_current_user


app = FastAPI(title="GridSense API")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# User context
# ---------------------------------------------------------------------------

def get_ctx(user: User = Depends(get_current_user)) -> store.Ctx:
    """The calling user's own data. Returns 409 'no_data' if they have none yet."""
    return store.get_context(user)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class Question(BaseModel):
    question: str


class Entry(BaseModel):
    """A reading the user confirmed (usually extracted from a photo)."""

    document_type: Literal[
        "prepaid_meter",
        "electricity_bill",
        "fuel_receipt",
        "appliance_label",
        "other",
    ]

    date: str | None = None
    kwh: float | None = None
    amount_ngn: float | None = None
    litres: float | None = None
    price_per_litre_ngn: float | None = None
    meter_reading_kwh: float | None = None
    appliance_watts: float | None = None

    summary: str = Field(default="", max_length=2000)
    confidence: Literal["low", "medium", "high"] | None = None
    image_path: str | None = Field(default=None, max_length=300)


class ProfileIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    account_name: str = Field(min_length=1, max_length=120)
    user_type: Literal["household", "business"]
    city: str = Field(min_length=1, max_length=80)
    state: str = Field(min_length=1, max_length=80)


class EnergyIn(BaseModel):
    currency: Literal["NGN", "USD", "GBP", "EUR"] = "NGN"

    grid_tariff: float = Field(ge=0, le=1_000_000)

    generator_cost_mode: Literal["per_kwh", "fuel"]

    generator_rate: float | None = Field(
        default=None,
        ge=0,
        le=10_000_000,
    )

    fuel_price_per_litre: float | None = Field(
        default=None,
        ge=0,
        le=10_000_000,
    )

    gen_litres_per_kwh: float | None = Field(
        default=None,
        gt=0,
        le=10,
    )

    @model_validator(mode="after")
    def mode_needs_its_fields(self):
        if (
            self.generator_cost_mode == "per_kwh"
            and self.generator_rate is None
        ):
            raise ValueError("Enter your generator cost per kWh")

        if self.generator_cost_mode == "fuel" and (
            self.fuel_price_per_litre is None
            or self.gen_litres_per_kwh is None
        ):
            raise ValueError(
                "Enter the fuel price and litres burned per kWh"
            )

        return self


class SettingsIn(BaseModel):
    profile: ProfileIn
    energy: EnergyIn


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"status": "GridSense API running"}


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

@app.get("/me")
def me(user: User = Depends(get_current_user)):
    """Who the server believes you are (from your verified token)."""
    return {
        "id": user.id,
        "email": user.email,
    }


@app.post("/demo-data")
def demo_data(user: User = Depends(get_current_user)):
    """Give a new user sample data in their own account."""
    n = store.seed_demo(user)
    return {"seeded": n}


@app.get("/settings")
def get_settings(user: User = Depends(get_current_user)):
    """The user's saved profile and energy settings."""
    return store.get_settings_payload(user)


@app.put("/settings")
def put_settings(
    body: SettingsIn,
    user: User = Depends(get_current_user),
):
    """Create or update the user's profile and energy settings."""
    return store.save_settings(
        user,
        body.profile.model_dump(),
        body.energy.model_dump(),
    )


# ---------------------------------------------------------------------------
# Analysis (this user's data only)
# ---------------------------------------------------------------------------

@app.get("/usage")
def usage(
    last_n_days: int = 14,
    ctx: store.Ctx = Depends(get_ctx),
):
    last_n_days = max(1, min(last_n_days, 90))

    df = ctx.df

    cutoff = (
        df["timestamp"].max()
        - pd.Timedelta(days=last_n_days)
    )

    out = df[
        df["timestamp"] > cutoff
    ][
        [
            "timestamp",
            "demand_kwh",
            "grid_kwh",
            "generator_kwh",
            "source",
        ]
    ].copy()

    out["timestamp"] = out["timestamp"].astype(str)

    return out.to_dict(orient="records")


@app.get("/daily")
def daily(
    last_n_days: int = 30,
    ctx: store.Ctx = Depends(get_ctx),
):
    return analysis.daily_totals(
        ctx.df,
        ctx.config,
    ).tail(
        max(1, min(last_n_days, 90))
    ).to_dict(orient="records")


@app.get("/peaks")
def peaks(ctx: store.Ctx = Depends(get_ctx)):
    return analysis.peak_analysis(ctx.df, ctx.config)


@app.get("/profile")
def profile(ctx: store.Ctx = Depends(get_ctx)):
    return analysis.hourly_profile(
        ctx.df
    ).to_dict(orient="records")


@app.get("/cost")
def cost(ctx: store.Ctx = Depends(get_ctx)):
    return analysis.cost_summary(ctx.df, ctx.config)


@app.get("/anomalies")
def anomalies(
    max_results: int = 10,
    ctx: store.Ctx = Depends(get_ctx),
):
    return advisor.tool_get_anomalies(
        ctx,
        max(1, min(max_results, 50)),
    )


@app.get("/forecast")
def get_forecast(ctx: store.Ctx = Depends(get_ctx)):
    try:
        return forecast.get_forecast(
            ctx.df,
            ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )


# ---------------------------------------------------------------------------
# AI (Muse Spark) running on this user's data
# ---------------------------------------------------------------------------

@app.post("/ask")
def ask(
    body: Question,
    user: User = Depends(get_current_user),
    ctx: store.Ctx = Depends(get_ctx),
):
    question = body.question.strip()

    if not question or len(question) > 1000:
        raise HTTPException(
            status_code=422,
            detail="Question must be 1-1000 characters",
        )

    result = advisor.ask_advisor(ctx, question)

    store.save_chat(
        user,
        question,
        result["answer"],
        result["tools_used"],
        result["mode"],
    )

    return result


# ---------------------------------------------------------------------------
# Photo extraction and logged readings
# ---------------------------------------------------------------------------

@app.post("/extract")
async def extract(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    image_bytes = await file.read()

    if len(image_bytes) > 8 * 1024 * 1024:
        return {
            "error": "Image too large (max 8 MB).",
            "mode": "error",
        }

    return advisor.extract_from_image(
        image_bytes,
        file.content_type or "image/jpeg",
    )


@app.post("/entries")
def save_entry(
    entry: Entry,
    user: User = Depends(get_current_user),
):
    return store.add_entry(
        user,
        entry.model_dump(),
    )


@app.get("/entries")
def entries(user: User = Depends(get_current_user)):
    return store.list_entries(user.token)


# ---------------------------------------------------------------------------
# Saved AI results
#
# Reading saved results never calls the AI.
# Only the /refresh routes run new analyses.
# ---------------------------------------------------------------------------

_busy: set = set()


def _run_once(user_id: str, kind: str, fn):
    """Prevent two tabs from running the same analysis simultaneously."""

    key = (user_id, kind)

    if key in _busy:
        raise HTTPException(
            status_code=429,
            detail="An analysis is already running",
        )

    _busy.add(key)

    try:
        return fn()
    finally:
        _busy.discard(key)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.get("/alerts/latest")
def alerts_latest(
    user: User = Depends(get_current_user),
    ctx: store.Ctx = Depends(get_ctx),
):
    return store.get_alerts_payload(user, ctx)


@app.post("/alerts/refresh")
def alerts_refresh(
    user: User = Depends(get_current_user),
    ctx: store.Ctx = Depends(get_ctx),
):
    _run_once(
        user.id,
        "alerts",
        lambda: store.save_alert_run(
            user,
            ctx,
            advisor.generate_alerts(ctx),
        ),
    )

    return store.get_alerts_payload(user, ctx)


@app.get("/alerts/history")
def alerts_history(user: User = Depends(get_current_user)):
    return store.alert_history(user.token)


# ---------------------------------------------------------------------------
# Savings plans
# ---------------------------------------------------------------------------

@app.get("/plans/latest")
def plans_latest(
    user: User = Depends(get_current_user),
    ctx: store.Ctx = Depends(get_ctx),
):
    return store.get_plan_payload(user, ctx)


@app.post("/plans/refresh")
def plans_refresh(
    user: User = Depends(get_current_user),
    ctx: store.Ctx = Depends(get_ctx),
):
    _run_once(
        user.id,
        "plan",
        lambda: store.save_plan_run(
            user,
            ctx,
            advisor.generate_plan(ctx),
        ),
    )

    return store.get_plan_payload(user, ctx)


@app.get("/plans/history")
def plans_history(user: User = Depends(get_current_user)):
    return store.plan_history(user.token)


# ---------------------------------------------------------------------------
# Chat and analysis history
# ---------------------------------------------------------------------------

@app.get("/chat/history")
def chat_history(user: User = Depends(get_current_user)):
    return store.chat_history(user.token)


@app.get("/analysis/history")
def analysis_history(user: User = Depends(get_current_user)):
    return store.analysis_history(user.token)

@app.get("/forecast/baseline")
def forecast_baseline(ctx: store.Ctx = Depends(get_ctx)):
    try:
        return analysis.daily_baseline(ctx.df, ctx.config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))