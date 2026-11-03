"""
main.py - GridSense API. Every endpoint except "/" requires a logged-in user.

Run:  uvicorn main:app --reload
Auth: send  Authorization: Bearer <Supabase access token>

Identity comes only from the verified token (see auth.py), never from a user id
supplied by the client. Each request loads that user's own readings and runs the
analysis and the AI agent against them.
"""

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import advisor
import analysis
import forecast
import store
from auth import User, get_current_user

app = FastAPI(title="GridSense API")

# Add your deployed frontend URL here (the .vercel.app pattern is already allowed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_ctx(user: User = Depends(get_current_user)) -> store.Ctx:
    """The calling user's own data. Returns 409 'no_data' if they have none yet."""
    return store.get_context(user)


class Question(BaseModel):
    question: str


class Entry(BaseModel):
    """A reading the user confirmed (usually extracted from a photo)."""
    document_type: str
    date: str | None = None
    kwh: float | None = None
    amount_ngn: float | None = None
    litres: float | None = None
    price_per_litre_ngn: float | None = None
    meter_reading_kwh: float | None = None
    appliance_watts: float | None = None
    summary: str = ""


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
    return {"id": user.id, "email": user.email}


@app.post("/demo-data")
def demo_data(user: User = Depends(get_current_user)):
    """Give a new user sample data in their own account (does nothing if they already have data)."""
    n = store.seed_demo(user)
    return {"seeded": n}


# ---------------------------------------------------------------------------
# Analysis (this user's data only)
# ---------------------------------------------------------------------------
@app.get("/usage")
def usage(last_n_days: int = 14, ctx: store.Ctx = Depends(get_ctx)):
    last_n_days = max(1, min(last_n_days, 90))
    df = ctx.df
    cutoff = df["timestamp"].max() - pd.Timedelta(days=last_n_days)
    out = df[df["timestamp"] > cutoff][["timestamp", "demand_kwh", "grid_kwh", "generator_kwh", "source"]].copy()
    out["timestamp"] = out["timestamp"].astype(str)
    return out.to_dict(orient="records")


@app.get("/daily")
def daily(last_n_days: int = 30, ctx: store.Ctx = Depends(get_ctx)):
    return analysis.daily_totals(ctx.df, ctx.config).tail(max(1, min(last_n_days, 90))).to_dict(orient="records")


@app.get("/peaks")
def peaks(ctx: store.Ctx = Depends(get_ctx)):
    return analysis.peak_analysis(ctx.df, ctx.config)


@app.get("/profile")
def profile(ctx: store.Ctx = Depends(get_ctx)):
    return analysis.hourly_profile(ctx.df).to_dict(orient="records")


@app.get("/cost")
def cost(ctx: store.Ctx = Depends(get_ctx)):
    return analysis.cost_summary(ctx.df, ctx.config)


@app.get("/anomalies")
def anomalies(max_results: int = 10, ctx: store.Ctx = Depends(get_ctx)):
    return advisor.tool_get_anomalies(ctx, max(1, min(max_results, 50)))


@app.get("/forecast")
def get_forecast(ctx: store.Ctx = Depends(get_ctx)):
    try:
        return forecast.get_forecast(ctx.df, ctx.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# AI (Muse Spark) running on this user's data
# ---------------------------------------------------------------------------
@app.post("/ask")
def ask(body: Question, user: User = Depends(get_current_user), ctx: store.Ctx = Depends(get_ctx)):
    question = body.question.strip()
    if not question or len(question) > 1000:
        raise HTTPException(status_code=422, detail="Question must be 1-1000 characters")
    result = advisor.ask_advisor(ctx, question)
    store.save_chat(user, question, result["answer"])
    return result


@app.get("/plan")
def plan(user: User = Depends(get_current_user), ctx: store.Ctx = Depends(get_ctx)):
    result = advisor.generate_plan(ctx)
    if result["mode"] == "live":
        store.save_plan(user, result["plan"])
    return result


@app.get("/alerts")
def alerts(ctx: store.Ctx = Depends(get_ctx)):
    return advisor.generate_alerts(ctx)


@app.post("/extract")
async def extract(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    image_bytes = await file.read()
    if len(image_bytes) > 8 * 1024 * 1024:
        return {"error": "Image too large (max 8 MB).", "mode": "error"}
    return advisor.extract_from_image(image_bytes, file.content_type or "image/jpeg")


@app.post("/entries")
def save_entry(entry: Entry, user: User = Depends(get_current_user)):
    return store.add_entry(user.id, entry.model_dump())


@app.get("/entries")
def entries(user: User = Depends(get_current_user)):
    return store.list_entries(user.id)
