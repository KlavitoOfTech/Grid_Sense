"""
advisor.py - GridSense runs on Meta's Muse Spark.

Muse Spark is the engine for four jobs:
  1. ASK      - tool-calling agent that answers questions from the user's real data
  2. PLAN     - tools + high reasoning effort, then a structured JSON weekly plan
  3. ALERTS   - "guardian": reviews anomalies/outages and returns structured alerts
  4. EXTRACT  - vision: reads a photo of a meter / bill / fuel receipt into structured
                data, which is logged and becomes visible to the agent as a tool

Docs used: Responses API, tool calling (https://dev.meta.ai/docs/tool-calling),
image understanding (input_image blocks), reasoning (reasoning={"effort": ...}).

Setup:  pip install openai
        backend/.env:  MODEL_API_KEY=...   (never commit it)
"""

import base64
import json
import os
import re

from dotenv import load_dotenv

import analysis
import forecast
import store

load_dotenv()

MODEL = "muse-spark-1.3"
BASE_URL = "https://api.meta.ai/v1"
MAX_TURNS = 6

SYSTEM_PROMPT = """You are GridSense, an energy advisor for households and small
businesses with unreliable grid power in Nigeria. Always call the tools to get the
user's real numbers before answering; never invent figures. Amounts are in Naira (NGN).
Be concise and practical: explain what the data shows, why it matters, and give
specific actions with an estimated saving. If data is missing, say so plainly."""


# ---------------------------------------------------------------------------
# Tools (run locally; Muse Spark decides when to call them)
# ---------------------------------------------------------------------------
def tool_get_cost_summary(ctx) -> dict:
    return analysis.cost_summary(ctx.df, ctx.config)


def tool_get_peaks(ctx) -> dict:
    return analysis.peak_analysis(ctx.df, ctx.config)


def tool_get_daily_totals(ctx, last_n_days: int = 7) -> list:
    last_n_days = max(1, min(int(last_n_days), 60))
    return analysis.daily_totals(ctx.df, ctx.config).tail(last_n_days).to_dict(orient="records")


def tool_get_anomalies(ctx, max_results: int = 10) -> list:
    """Hours far above normal for that hour of day (median/MAD z-score)."""
    df = ctx.df.copy()
    df["hour"] = df["timestamp"].dt.hour
    grp = df.groupby("hour")["demand_kwh"]
    median = grp.transform("median")
    mad = grp.transform(lambda s: (s - s.median()).abs().median()).replace(0, 0.05)
    df["score"] = 0.6745 * (df["demand_kwh"] - median) / mad
    flagged = df[df["score"] > 8].sort_values("score", ascending=False).head(int(max_results))
    return [
        {
            "timestamp": str(r.timestamp),
            "demand_kwh": round(float(r.demand_kwh), 2),
            "normal_kwh_for_hour": round(float(median.loc[r.Index]), 2),
            "source": r.source,
        }
        for r in flagged.itertuples()
    ]


def tool_simulate_scenario(ctx, kwh_per_day_to_shift: float = 1.0) -> dict:
    """What-if: move daily generator energy to hours when the grid is on."""
    kwh = float(kwh_per_day_to_shift)
    s = analysis.cost_summary(ctx.df, ctx.config)
    gen_per_day = s["generator_kwh"] / s["days"]
    shifted = min(max(kwh, 0.0), gen_per_day)
    per_kwh_saving = s["generator_rate_per_kwh"] - s["grid_rate_per_kwh"]
    daily = shifted * per_kwh_saving
    return {
        "kwh_per_day_shifted": round(shifted, 2),
        "avg_generator_kwh_per_day": round(gen_per_day, 2),
        "estimated_saving_per_day_ngn": round(daily),
        "estimated_saving_per_30_days_ngn": round(daily * 30),
        "note": "Assumes the shifted load can be run while the grid is available.",
    }


def tool_get_logged_entries(ctx) -> list:
    """Readings the user logged, e.g. from photos of meters, bills, fuel receipts."""
    rows = store.list_entries(ctx.token, limit=20)
    return [{k: v for k, v in r.items() if k != "image_path"} for r in rows]

def tool_get_forecast(ctx) -> dict:
    """Expected usage for the next 24 hours, with how accurate the model has been."""
    f = forecast.get_forecast(ctx.df, ctx.user_id)
    return {"method": f["method"], "metrics": f["metrics"], "next_24h_kwh": f["next_24h_kwh"],
            "peak_hour": f["peak_hour"], "hourly_kwh": [h["kwh"] for h in f["hours"]]}


TOOL_FUNCTIONS = {
    "get_forecast": tool_get_forecast,
    "get_cost_summary": tool_get_cost_summary,
    "get_peaks": tool_get_peaks,
    "get_daily_totals": tool_get_daily_totals,
    "get_anomalies": tool_get_anomalies,
    "simulate_scenario": tool_simulate_scenario,
    "get_logged_entries": tool_get_logged_entries,
}

TOOLS = [
    {"type": "function", "name": "get_forecast",
     "description": "Forecast of the user's energy use for the next 24 hours (hourly kWh, total, "
                    "peak hour) and the model's measured accuracy.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "get_cost_summary",
     "description": "Overall energy use and cost: grid vs generator kWh and NGN, generator "
                    "share, outage hours, 30-day projection, and rates.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "get_peaks",
     "description": "Peak usage analysis: busiest hours, peak day, evening-peak share, "
                    "load factor, weekday vs weekend use.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"type": "function", "name": "get_daily_totals",
     "description": "Daily kWh, outage hours and cost for the most recent days.",
     "parameters": {"type": "object",
                    "properties": {"last_n_days": {"type": "integer",
                                                   "description": "How many recent days (1-60)."}},
                    "required": ["last_n_days"]}},
    {"type": "function", "name": "get_anomalies",
     "description": "Hours with unusually high consumption versus the normal pattern for that hour.",
     "parameters": {"type": "object",
                    "properties": {"max_results": {"type": "integer",
                                                   "description": "Max anomalies to return."}},
                    "required": ["max_results"]}},
    {"type": "function", "name": "simulate_scenario",
     "description": "What-if: estimate savings from shifting a given amount of daily energy "
                    "from the generator to hours when grid power is available.",
     "parameters": {"type": "object",
                    "properties": {"kwh_per_day_to_shift": {"type": "number",
                                   "description": "kWh per day moved from generator to grid hours."}},
                    "required": ["kwh_per_day_to_shift"]}},
    {"type": "function", "name": "get_logged_entries",
     "description": "Readings the user logged from photos of meters, bills or fuel receipts.",
     "parameters": {"type": "object", "properties": {}, "additionalProperties": False}},
]


def run_tool(ctx, name: str, arguments_json: str) -> str:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        args = json.loads(arguments_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
        return json.dumps(fn(ctx, **args), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# Structured-output schemas
# ---------------------------------------------------------------------------
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "when": {"type": "string"},
                    "do": {"type": "string"},
                    "why": {"type": "string"},
                    "est_saving_ngn_per_month": {"type": "number"},
                    "effort": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["title", "when", "do", "why", "est_saving_ngn_per_month", "effort"],
                "additionalProperties": False,
            },
        },
        "total_est_saving_ngn_per_month": {"type": "number"},
        "watch_out": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "summary", "actions", "total_est_saving_ngn_per_month", "watch_out"],
    "additionalProperties": False,
}

ALERTS_SCHEMA = {
    "type": "object",
    "properties": {
        "alerts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                    "title": {"type": "string"},
                    "what_happened": {"type": "string"},
                    "likely_cause": {"type": "string"},
                    "action": {"type": "string"},
                },
                "required": ["severity", "title", "what_happened", "likely_cause", "action"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["alerts"],
    "additionalProperties": False,
}

NUM_OR_NULL = {"type": ["number", "null"]}
STR_OR_NULL = {"type": ["string", "null"]}

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string", "enum": [
            "prepaid_meter", "electricity_bill", "fuel_receipt", "appliance_label", "other"]},
        "date": STR_OR_NULL,
        "kwh": NUM_OR_NULL,
        "amount_ngn": NUM_OR_NULL,
        "litres": NUM_OR_NULL,
        "price_per_litre_ngn": NUM_OR_NULL,
        "meter_reading_kwh": NUM_OR_NULL,
        "appliance_watts": NUM_OR_NULL,
        "summary": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["document_type", "date", "kwh", "amount_ngn", "litres", "price_per_litre_ngn",
                 "meter_reading_kwh", "appliance_watts", "summary", "confidence"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def _get_client():
    from openai import OpenAI  # imported lazily so the app still starts without it

    return OpenAI(base_url=BASE_URL, api_key=os.environ["MODEL_API_KEY"])


def _parse_json(text: str) -> dict:
    """Parse JSON from model text, tolerating code fences or stray prose."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        return json.loads(text[start:end + 1])


def _check(obj: dict, schema: dict) -> dict:
    missing = [k for k in schema["required"] if k not in obj]
    if missing:
        raise ValueError(f"Model output missing fields: {missing}")
    return obj


def _reasoning(effort):
    return {"reasoning": {"effort": effort}} if effort else {}


def _run_tool_loop(client, ctx, prompt, effort=None):
    """Let Muse Spark call tools until it stops asking. Returns (last_response, tools_used)."""
    tools_used = []
    response = client.responses.create(
        model=MODEL, instructions=SYSTEM_PROMPT, input=prompt, tools=TOOLS, **_reasoning(effort))
    for _ in range(MAX_TURNS):
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            break
        outputs = []
        for call in calls:
            tools_used.append(call.name)
            outputs.append({"type": "function_call_output", "call_id": call.call_id,
                            "output": run_tool(ctx, call.name, call.arguments)})
        response = client.responses.create(
            model=MODEL, instructions=SYSTEM_PROMPT, input=outputs,
            previous_response_id=response.id, tools=TOOLS, **_reasoning(effort))
    return response, tools_used


def _structured(client, schema_name, schema, input_, previous_response_id=None, effort=None):
    """
    Get JSON matching `schema` from Muse Spark.
    First try native structured output (text.format json_schema); if the API rejects that
    syntax, retry asking for JSON-only in the prompt and parse it ourselves.
    """
    base = {"model": MODEL, "instructions": SYSTEM_PROMPT, **_reasoning(effort)}
    if previous_response_id:
        base["previous_response_id"] = previous_response_id
    try:
        resp = client.responses.create(
            input=input_, **base,
            text={"format": {"type": "json_schema", "name": schema_name,
                             "schema": schema, "strict": True}})
        return _check(_parse_json(resp.output_text), schema)
    except Exception as exc:
        print(f"[advisor] native structured output failed ({exc}); retrying with JSON prompt")
        suffix = ("\n\nReply with ONLY a JSON object matching this JSON Schema, no other text:\n"
                  + json.dumps(schema))
        if isinstance(input_, str):
            input_ = input_ + suffix
        else:  # multimodal message list: append the instruction to the text block
            input_ = json.loads(json.dumps(input_))
            for block in input_[0]["content"]:
                if block.get("type") == "input_text":
                    block["text"] += suffix
        resp = client.responses.create(input=input_, **base)
        return _check(_parse_json(resp.output_text), schema)


# ---------------------------------------------------------------------------
# 1. ASK: tool-calling agent
# ---------------------------------------------------------------------------
def ask_advisor(ctx, question: str, client=None) -> dict:
    tools_used = []
    try:
        client = client or _get_client()
        response, tools_used = _run_tool_loop(client, ctx, question)
        answer = (response.output_text or "").strip()
        if not answer:
            raise RuntimeError("Empty answer from model")
        return {"answer": answer, "tools_used": tools_used, "mode": "live"}
    except Exception as exc:
        print(f"[advisor] ask fallback: {exc}")
        return {"answer": fallback_answer(ctx), "tools_used": tools_used, "mode": "fallback"}


# ---------------------------------------------------------------------------
# 2. PLAN: tools + high reasoning, then structured weekly plan
# ---------------------------------------------------------------------------
def generate_plan(ctx, client=None) -> dict:
    tools_used = []
    try:
        client = client or _get_client()
        response, tools_used = _run_tool_loop(
            client, ctx,
            "Build a practical plan to cut this user's energy costs. Investigate their "
            "costs, peaks, recent days, anomalies and any logged readings, and test at "
            "least one what-if scenario.",
            effort="high")
        plan = _structured(
            client, "energy_plan", PLAN_SCHEMA,
            "Now give the final plan as JSON: a short headline, a summary, 2-5 concrete "
            "actions (when to do it, what to do, why, estimated NGN saving per month, "
            "effort), the total estimated monthly saving, and any warnings. Use only "
            "numbers you got from the tools.",
            previous_response_id=response.id, effort="high")
        return {"plan": plan, "tools_used": tools_used, "mode": "live"}
    except Exception as exc:
        print(f"[advisor] plan fallback: {exc}")
        return {"plan": fallback_plan(ctx), "tools_used": tools_used, "mode": "fallback"}


# ---------------------------------------------------------------------------
# 3. ALERTS: the guardian
# ---------------------------------------------------------------------------
def generate_alerts(ctx, client=None) -> dict:
    tools_used = []
    try:
        client = client or _get_client()
        response, tools_used = _run_tool_loop(
            client, ctx,
            "Act as a guardian. Check recent anomalies, the last 7 days of usage and "
            "outage hours, and the generator share. Decide what the user should be warned about.",
            effort="medium")
        out = _structured(
            client, "alerts", ALERTS_SCHEMA,
            "Return the alerts as JSON. Only include real issues found in the data, "
            "most severe first. If everything looks normal, return one 'info' alert saying so.",
            previous_response_id=response.id, effort="medium")
        return {"alerts": out["alerts"], "tools_used": tools_used, "mode": "live"}
    except Exception as exc:
        print(f"[advisor] alerts fallback: {exc}")
        return {"alerts": fallback_alerts(ctx), "tools_used": tools_used, "mode": "fallback"}


# ---------------------------------------------------------------------------
# 4. EXTRACT: vision -> structured data
# ---------------------------------------------------------------------------
def extract_from_image(image_bytes: bytes, mime_type: str = "image/jpeg", client=None) -> dict:
    """Read a photo of a prepaid meter, bill, fuel receipt or appliance label."""
    if not mime_type.startswith("image/"):
        return {"error": "Please upload an image file.", "mode": "error"}
    try:
        client = client or _get_client()
        b64 = base64.b64encode(image_bytes).decode()
        content = [
            {"type": "input_text",
             "text": ("Read this photo from a Nigerian household: a prepaid meter, electricity "
                      "bill, generator fuel receipt, or appliance label. Extract the values you "
                      "can actually see. Use null for anything not visible; never guess. "
                      "Amounts are in NGN.")},
            {"type": "input_image", "image_url": f"data:{mime_type};base64,{b64}"},
        ]
        data = _structured(client, "reading", EXTRACT_SCHEMA,
                           [{"type": "message", "role": "user", "content": content}],
                           effort="low")
        return {"reading": data, "mode": "live"}
    except Exception as exc:
        print(f"[advisor] extract failed: {exc}")
        return {"error": "Could not read the image. Please enter the values manually.",
                "mode": "error"}


# ---------------------------------------------------------------------------
# Fallbacks (so the demo never breaks)
# ---------------------------------------------------------------------------
def fallback_answer(ctx) -> str:
    c = analysis.cost_summary(ctx.df, ctx.config)
    p = analysis.peak_analysis(ctx.df, ctx.config)
    top = p["top_peak_hours"][0]["hour"]
    return (
        f"Over {c['days']} days you used {c['total_kwh']} kWh costing about NGN "
        f"{c['total_cost']:,}. The generator supplied {c['generator_share_pct']}% of your "
        f"energy but cost about {c['generator_to_grid_cost_ratio']}x more per kWh than the "
        f"grid. Your busiest hour is {top}:00. Shifting heavy tasks to hours when grid power "
        f"is available is your biggest saving opportunity. (Offline summary - AI advisor unavailable.)")


def fallback_plan(ctx) -> dict:
    s = tool_simulate_scenario(ctx, 1.5)
    return {
        "headline": "Shift heavy use to grid hours",
        "summary": "The generator is your most expensive source. Running heavy appliances "
                   "while grid power is on cuts your bill. (Offline plan - AI unavailable.)",
        "actions": [{
            "title": "Run heavy appliances on grid power",
            "when": "Whenever the grid is on",
            "do": "Do laundry, ironing and water pumping when NEPA is available.",
            "why": "Generator energy costs several times more per kWh than grid energy.",
            "est_saving_ngn_per_month": s["estimated_saving_per_30_days_ngn"],
            "effort": "low",
        }],
        "total_est_saving_ngn_per_month": s["estimated_saving_per_30_days_ngn"],
        "watch_out": [],
    }


def fallback_alerts(ctx) -> list:
    out = []
    anomalies = tool_get_anomalies(ctx, 3)
    if anomalies:
        a = anomalies[0]
        out.append({
            "severity": "warning", "title": "Unusual consumption detected",
            "what_happened": f"{a['demand_kwh']} kWh at {a['timestamp']} vs about "
                             f"{a['normal_kwh_for_hour']} kWh normally.",
            "likely_cause": "An appliance left on, a faulty appliance, or a meter issue.",
            "action": "Check what was running then and unplug anything not needed.",
        })
    if not out:
        out.append({"severity": "info", "title": "Everything looks normal",
                    "what_happened": "No unusual usage found.", "likely_cause": "-",
                    "action": "No action needed."})
    return out
