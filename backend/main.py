"""
main.py - GridSense API.

Run:  uvicorn main:app --reload
Docs: http://localhost:8000/docs

Needs: pip install fastapi uvicorn python-multipart openai
"""

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import advisor
import analysis

app = FastAPI(title="GridSense API")

# Allow the React dev server (and your deployed frontend) to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_origin_regex=r"https://.*\.vercel\.app",
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


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


@app.get("/")
def root():
    return {"status": "GridSense API running"}


# ----- Muse Spark agent endpoints (the heart of the product) -----------------
@app.get("/agent/briefing")
def briefing(force: bool = False):
    """The agent investigates the data on its own and returns headline, alerts, recommendations."""
    return advisor.daily_briefing(force=force)


@app.post("/agent/ask")
def ask(body: Question):
    """Ask the agent anything; it calls tools and replies with a structured report."""
    return advisor.ask_advisor(body.question)


@app.post("/agent/photo")
async def photo(file: UploadFile = File(...)):
    """Upload a photo of a meter, bill or fuel receipt; the agent reads and logs it."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Please upload an image file.")
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image is larger than 5 MB.")
    return advisor.read_photo(data, file.content_type)


@app.get("/readings")
def readings():
    """Values the agent has read from photos so far."""
    return advisor.READINGS


# ----- Plain data endpoints (feed the charts) --------------------------------
@app.get("/usage")
def usage(last_n_days: int = 14):
    """Hourly usage for the last N days (for the usage chart)."""
    df = advisor.get_df()
    cutoff = df["timestamp"].max() - pd.Timedelta(days=last_n_days)
    out = df[df["timestamp"] > cutoff][
        ["timestamp", "demand_kwh", "grid_kwh", "generator_kwh", "source", "temp_c"]
    ].copy()
    out["timestamp"] = out["timestamp"].astype(str)
    return out.to_dict(orient="records")


@app.get("/daily")
def daily(last_n_days: int = 30):
    return analysis.daily_totals(advisor.get_df()).tail(last_n_days).to_dict(orient="records")


@app.get("/peaks")
def peaks():
    return analysis.peak_analysis(advisor.get_df())


@app.get("/profile")
def profile():
    return analysis.hourly_profile(advisor.get_df()).to_dict(orient="records")


@app.get("/cost")
def cost():
    return analysis.cost_summary(advisor.get_df())


@app.get("/anomalies")
def anomalies(max_results: int = 10):
    return advisor.tool_get_anomalies(max_results)


@app.get("/forecast")
def forecast(hours_ahead: int = 24):
    return advisor.tool_get_forecast(hours_ahead)


@app.get("/grid-availability")
def grid_availability():
    return advisor.tool_get_grid_availability()


@app.get("/plan")
def plan():
    """Muse Spark investigates the data with tools, then returns a structured savings plan."""
    return advisor.generate_plan()


@app.get("/alerts")
def alerts():
    """Guardian: Muse Spark reviews recent data and returns structured alerts."""
    return advisor.generate_alerts()


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    """Read a photo of a meter, bill or fuel receipt with Muse Spark vision."""
    image_bytes = await file.read()
    if len(image_bytes) > 8 * 1024 * 1024:
        return {"error": "Image too large (max 8 MB).", "mode": "error"}
    return advisor.extract_from_image(image_bytes, file.content_type or "image/jpeg")


@app.post("/entries")
def save_entry(entry: Entry):
    """Log a confirmed reading so the agent can use it."""
    return advisor.add_entry(entry.model_dump())


@app.get("/entries")
def entries():
    return advisor.list_entries()
