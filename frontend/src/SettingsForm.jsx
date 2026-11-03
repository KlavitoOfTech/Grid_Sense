import { useState } from "react";
import { put } from "./api";
import { CURRENCIES, money } from "./money";

function toForm(s) {
  const p = s?.profile ?? {};
  const e = s?.energy ?? {};
  const str = (v) => (v == null ? "" : String(v));
  return {
    account_name: p.account_name ?? "",
    user_type: p.user_type ?? "household",
    city: p.city ?? "",
    state: p.state ?? "",
    currency: e.currency ?? "NGN",
    grid_tariff: str(e.grid_tariff),
    generator_cost_mode: e.generator_cost_mode ?? "per_kwh",
    generator_rate: str(e.generator_rate),
    fuel_price_per_litre: str(e.fuel_price_per_litre),
    gen_litres_per_kwh: str(e.gen_litres_per_kwh),
  };
}

const toNum = (v) => (v === "" || v == null ? null : Number(v));

/** Used by onboarding (wizard = 2 steps) and by the settings page (one page). */
export default function SettingsForm({ initial, wizard = false, submitLabel = "Save settings", onSaved }) {
  const [f, setF] = useState(() => toForm(initial));
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));
  const fuel = f.generator_cost_mode === "fuel";

  const aboutOk = f.account_name.trim() && f.city.trim() && f.state.trim();

  const energyError = () => {
    const tariff = toNum(f.grid_tariff);
    if (tariff == null || tariff < 0) return "Enter your electricity tariff per kWh.";
    if (!fuel) {
      const r = toNum(f.generator_rate);
      if (r == null || r < 0) return "Enter your generator cost per kWh.";
    } else {
      const p = toNum(f.fuel_price_per_litre);
      const l = toNum(f.gen_litres_per_kwh);
      if (p == null || p < 0 || l == null || !(l > 0)) return "Enter the fuel price and the litres burned per kWh.";
    }
    return "";
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!aboutOk) { setErr("Fill in your name, city and state."); setStep(0); return; }
    const msg = energyError();
    if (msg) { setErr(msg); return; }
    setBusy(true); setErr("");
    try {
      const saved = await put("/settings", {
        profile: {
          account_name: f.account_name.trim(),
          user_type: f.user_type,
          city: f.city.trim(),
          state: f.state.trim(),
        },
        energy: {
          currency: f.currency,
          grid_tariff: toNum(f.grid_tariff),
          generator_cost_mode: f.generator_cost_mode,
          generator_rate: fuel ? null : toNum(f.generator_rate),
          fuel_price_per_litre: fuel ? toNum(f.fuel_price_per_litre) : null,
          gen_litres_per_kwh: fuel ? toNum(f.gen_litres_per_kwh) : null,
        },
      });
      onSaved(saved);
    } catch (ex) {
      setErr(ex.message || "Couldn’t save. Try again.");
    }
    setBusy(false);
  };

  const showAbout = !wizard || step === 0;
  const showEnergy = !wizard || step === 1;
  const nameLabel = f.user_type === "business" ? "Business name" : "Household name";
  const fuelOnly = toNum(f.fuel_price_per_litre) * toNum(f.gen_litres_per_kwh);

  return (
    <form onSubmit={submit}>
      {wizard && <p className="muted">Step {step + 1} of 2 · {step === 0 ? "About you" : "Your energy costs"}</p>}

      {showAbout && (
        <section>
          {!wizard && <h2>About you</h2>}
          <div className="fields">
            <label>I am a
              <select value={f.user_type} onChange={(e) => set("user_type", e.target.value)}>
                <option value="household">Household</option>
                <option value="business">Business</option>
              </select>
            </label>
            <label>{nameLabel}
              <input value={f.account_name} maxLength={120} onChange={(e) => set("account_name", e.target.value)} />
            </label>
            <label>City
              <input value={f.city} maxLength={80} onChange={(e) => set("city", e.target.value)} />
            </label>
            <label>State
              <input value={f.state} maxLength={80} onChange={(e) => set("state", e.target.value)} />
            </label>
          </div>
        </section>
      )}

      {showEnergy && (
        <section>
          {!wizard && <h2>Your energy costs</h2>}
          <div className="fields">
            <label>Preferred currency
              <select value={f.currency} onChange={(e) => set("currency", e.target.value)}>
                {CURRENCIES.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
              </select>
            </label>
            <label>Electricity tariff (per kWh)
              <input type="number" min="0" step="0.01" value={f.grid_tariff}
                onChange={(e) => set("grid_tariff", e.target.value)} />
            </label>
          </div>
          <p className="muted">Check your latest electricity bill or your DisCo’s published rate for your band.</p>

          <p><strong>Generator cost</strong></p>
          <label><input type="radio" name="mode" checked={!fuel} onChange={() => set("generator_cost_mode", "per_kwh")} />{" "}
            I know my generator cost per kWh</label>
          <br />
          <label><input type="radio" name="mode" checked={fuel} onChange={() => set("generator_cost_mode", "fuel")} />{" "}
            Estimate it from fuel</label>

          <div className="fields">
            {!fuel ? (
              <label>Generator cost (per kWh)
                <input type="number" min="0" step="0.01" value={f.generator_rate}
                  onChange={(e) => set("generator_rate", e.target.value)} />
              </label>
            ) : (
              <>
                <label>Fuel price (per litre)
                  <input type="number" min="0" step="0.01" value={f.fuel_price_per_litre}
                    onChange={(e) => set("fuel_price_per_litre", e.target.value)} />
                </label>
                <label>Litres burned per kWh
                  <input type="number" min="0.01" step="0.01" value={f.gen_litres_per_kwh}
                    onChange={(e) => set("gen_litres_per_kwh", e.target.value)} />
                </label>
              </>
            )}
          </div>
          {fuel && fuelOnly > 0 && (
            <p className="muted">Fuel alone is about {money(fuelOnly, f.currency)} per kWh. GridSense adds a small maintenance allowance on top.</p>
          )}
          {f.currency !== "NGN" && (
            <p className="muted">Note: GridSense’s AI advice and photo reading still quote amounts in Naira.</p>
          )}
        </section>
      )}

      {err && <p role="alert" style={{ color: "#b3261e" }}>{err}</p>}

      <p>
        {wizard && step === 0 ? (
          <button type="button" disabled={!aboutOk} onClick={() => { setErr(""); setStep(1); }}>Next</button>
        ) : (
          <>
            {wizard && <button type="button" className="chip" onClick={() => setStep(0)}>Back</button>}{" "}
            <button type="submit" disabled={busy}>{busy ? "Saving…" : submitLabel}</button>
          </>
        )}
      </p>
    </form>
  );
}