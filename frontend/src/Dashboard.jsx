
import { useEffect, useState } from "react";
import { supabase } from "./supabase";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  AreaChart,
  Area,
} from "recharts";

import { money, setCurrency } from "./money";
import { AlertsPanel, PlanPanel, NavButtons, BaselinePanel } from "./Pages";
import Splash from "./Splash";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

const naira = (n) => money(n);

// Every request carries the user's Supabase access token.
async function api(path, options = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;

  const res = await fetch(API + path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${token}`,
    },
  });

  if (res.status === 401) {
    await supabase.auth.signOut();
    throw new Error("Session expired");
  }

  if (!res.ok) {
    const e = new Error(`${path} failed (${res.status})`);
    e.status = res.status;
    throw e;
  }

  return res.json();
}

const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

function Badge({ mode }) {
  if (!mode) return null;

  return (
    <span className={`badge ${mode === "live" ? "live" : "off"}`}>
      {mode === "live" ? "Muse Spark" : "Offline summary"}
    </span>
  );
}

function Tools({ used }) {
  if (!used?.length) return null;

  return (
    <p className="tools">
      Checked:{" "}
      {[...new Set(used)]
        .map((t) => t.replace("get_", "").replaceAll("_", " "))
        .join(", ")}
    </p>
  );
}

/* Power timeline */
function Timeline({ usage, anomalies }) {
  const flagged = new Set(anomalies.map((a) => a.timestamp));
  const days = {};

  usage.forEach((r) => {
    const [d, t] = r.timestamp.split(" ");
    (days[d] ||= Array(24).fill(null))[Number(t.slice(0, 2))] = r;
  });

  return (
    <div
      className="timeline"
      role="img"
      aria-label="Hourly power source for the last 14 days"
    >
      <div className="trow axis">
        <span />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={h}>{h % 6 === 0 ? h : ""}</span>
        ))}
      </div>

      {Object.entries(days).map(([d, cells]) => (
        <div className="trow" key={d}>
          <span className="dlabel">
            {new Date(d).toLocaleDateString("en-GB", {
              weekday: "short",
              day: "numeric",
            })}
          </span>

          {cells.map((r, h) => (
            <span
              key={h}
              className={`cell ${r?.source ?? "none"}${
                r && flagged.has(r.timestamp) ? " flag" : ""
              }`}
              title={
                r
                  ? `${d} ${h}:00 · ${r.source} · ${r.demand_kwh} kWh`
                  : ""
              }
            />
          ))}
        </div>
      ))}

      <div className="legend">
        <span><i className="cell grid" /> Grid</span>
        <span><i className="cell generator" /> Generator</span>
        <span><i className="cell none" /> No power</span>
        <span><i className="cell none flag" /> Unusual use</span>
      </div>
    </div>
  );
}

const FIELDS = [
  ["amount_ngn", "Amount (₦)"],
  ["kwh", "Energy (kWh)"],
  ["litres", "Fuel (litres)"],
  ["price_per_litre_ngn", "Price per litre (₦)"],
  ["meter_reading_kwh", "Meter reading (kWh)"],
  ["appliance_watts", "Appliance power (W)"],
];

const ALLOWED = ["image/jpeg", "image/png", "image/webp"];
const EXT = {
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
};
const MAX_BYTES = 8 * 1024 * 1024;

function Reading({ onSaved }) {
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(null);
  const [file, setFile] = useState(null);
  const [msg, setMsg] = useState("");

  const upload = async (e) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;

    if (!ALLOWED.includes(f.type)) {
      setMsg("Please use a JPG, PNG or WebP photo.");
      return;
    }

    if (f.size > MAX_BYTES) {
      setMsg("That photo is too large (max 8 MB).");
      return;
    }

    setBusy(true);
    setMsg("");
    setForm(null);
    setFile(null);

    const body = new FormData();
    body.append("file", f);

    try {
      const r = await api("/extract", { method: "POST", body });

      if (r.reading) {
        setForm(r.reading);
        setFile(f);
      } else {
        setMsg(r.error || "Couldn’t read that photo. Try a clearer one.");
      }
    } catch {
      setMsg("Couldn’t reach the API.");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const num = (v) => (v === "" || v == null ? null : Number(v));

    setSaving(true);
    setMsg("");
    let path = null;

    try {
      if (file) {
        const { data } = await supabase.auth.getSession();
        const uid = data.session?.user?.id;

        if (!uid) throw new Error("Not signed in");

        path = `${uid}/${crypto.randomUUID()}.${EXT[file.type]}`;

        const { error } = await supabase.storage
          .from("meter-photos")
          .upload(path, file, { contentType: file.type });

        if (error) {
          path = null;
          setMsg("Couldn’t upload the photo. Try again.");
          return;
        }
      }

      const payload = {
        document_type: form.document_type,
        date: form.date || null,
        summary: form.summary || "",
        confidence: form.confidence || null,
        image_path: path,
      };

      FIELDS.forEach(([k]) => {
        payload[k] = num(form[k]);
      });

      await post("/entries", payload);

      setForm(null);
      setFile(null);
      setMsg("Saved. GridSense will use this reading in its answers.");
      onSaved();
    } catch {
      if (path) {
        await supabase.storage.from("meter-photos").remove([path]);
      }

      setMsg("Couldn’t save. Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section>
      <h2>Add a reading from a photo</h2>

      <p className="muted">
        Snap a prepaid meter, electricity bill or fuel receipt. Check the
        values before saving.
      </p>

      <label className="filebtn">
        {busy ? "Reading photo…" : "Choose or take a photo"}

        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          capture="environment"
          onChange={upload}
          disabled={busy || saving}
          hidden
        />
      </label>

      {msg && <p className="muted">{msg}</p>}

      {form && (
        <div className="reading">
          <p>
            <strong>{form.document_type.replaceAll("_", " ")}</strong>{" "}
            <span className="muted">· confidence {form.confidence}</span>
          </p>

          <p className="muted">{form.summary}</p>

          <div className="fields">
            {FIELDS.map(([k, label]) => (
              <label key={k}>
                {label}
                <input
                  type="number"
                  value={form[k] ?? ""}
                  onChange={(e) =>
                    setForm({ ...form, [k]: e.target.value })
                  }
                />
              </label>
            ))}
          </div>

          <button onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save reading"}
          </button>
        </div>
      )}
    </section>
  );
}

function LoggedReadings({ entries }) {
  const [preview, setPreview] = useState(null);

  if (!entries.length) return null;

  const sum = (k) =>
    entries.reduce((s, e) => s + (Number(e[k]) || 0), 0);

  const view = async (e) => {
    const { data, error } = await supabase.storage
      .from("meter-photos")
      .createSignedUrl(e.image_path, 60);

    if (!error && data?.signedUrl) {
      setPreview({ id: e.id, url: data.signedUrl });
    }
  };

  return (
    <section>
      <h2>Your logged readings</h2>

      <p className="muted">
        {entries.length} logged · ₦
        {Math.round(sum("amount_ngn")).toLocaleString()} spent ·{" "}
        {Math.round(sum("kwh") * 10) / 10} kWh ·{" "}
        {Math.round(sum("litres") * 10) / 10} litres of fuel
      </p>

      <ul className="actions">
        {entries.slice(0, 10).map((e) => (
          <li key={e.id}>
            <div>
              <strong>{e.document_type.replaceAll("_", " ")}</strong>

              <span className="muted">
                {" "}· {new Date(e.created_at).toLocaleDateString("en-GB")}
              </span>

              <p className="muted">{e.summary}</p>

              {e.image_path && (
                <button className="chip" onClick={() => view(e)}>
                  View photo
                </button>
              )}

              {preview?.id === e.id && (
                <img
                  src={preview.url}
                  alt="Uploaded reading"
                  style={{ maxWidth: "100%", marginTop: 8 }}
                />
              )}
            </div>

            <div className="save">
              {e.amount_ngn != null
                ? `₦${Number(e.amount_ngn).toLocaleString()}`
                : ""}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Charts({ daily, profile, forecast }) {
  return (
    <section>
      <h2>Where the money goes</h2>

      <div className="charts">
        <div>
          <h3>Daily cost, grid vs generator</h3>

          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={daily.slice(-30)}>
              <XAxis
                dataKey="date"
                tickFormatter={(d) => d.slice(5)}
                fontSize={11}
                interval={6}
              />

              <YAxis
                fontSize={11}
                width={44}
                tickFormatter={(v) => `${v / 1000}k`}
              />

              <Tooltip formatter={(v) => naira(v)} />

              <Bar
                dataKey="grid_cost"
                stackId="a"
                fill="#1f8a5b"
                name="Grid"
              />

              <Bar
                dataKey="generator_cost"
                stackId="a"
                fill="#e4572e"
                name="Generator"
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div>
          <h3>Average use by hour of day (kWh)</h3>

          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={profile}>
              <XAxis dataKey="hour" fontSize={11} interval={3} />
              <YAxis fontSize={11} width={32} />
              <Tooltip />

              <Area
                dataKey="avg_kwh"
                stroke="#12243a"
                fill="#12243a22"
                name="kWh"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {forecast && (
        <div className="fc">
          <h3>
            Next 24 hours: about {forecast.next_24h_kwh} kWh, peaking at{" "}
            {forecast.peak_hour.timestamp.slice(11, 16)}
          </h3>

          <ResponsiveContainer width="100%" height={180}>
            <AreaChart
              data={forecast.hours.map((h) => ({
                hour: h.timestamp.slice(11, 16),
                kwh: h.kwh,
              }))}
            >
              <XAxis dataKey="hour" fontSize={11} interval={3} />
              <YAxis fontSize={11} width={32} />
              <Tooltip />

              <Area
                dataKey="kwh"
                stroke="#1f8a5b"
                fill="#1f8a5b33"
                name="kWh"
              />
            </AreaChart>
          </ResponsiveContainer>

          <p className="muted">
            {forecast.method === "gru"
              ? "PyTorch GRU model"
              : "Same-hour average of the last 7 days"}
            . Average error on {forecast.metrics.test_days} unseen days:{" "}
            {forecast.method === "gru" &&
            forecast.metrics.gru_mae_kwh != null
              ? `${forecast.metrics.gru_mae_kwh} kWh per hour, ${forecast.metrics.improvement_pct}% better than the simple baseline.`
              : `${forecast.metrics.baseline_mae_kwh} kWh per hour.`}
          </p>
        </div>
      )}
    </section>
  );
}

export default function Dashboard({
  user,
  onLogout,
  onOpenSettings,
}) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(false);
  const [empty, setEmpty] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [entries, setEntries] = useState([]);

  const seed = async () => {
    setSeeding(true);

    try {
      await api("/demo-data", { method: "POST" });
      window.location.reload();
    } catch {
      setSeeding(false);
      setErr(true);
    }
  };

  const loadEntries = () =>
    api("/entries")
      .then((e) => setEntries(e))
      .catch(() => {});

  useEffect(() => {
    Promise.all([
      api("/cost"),
      api("/usage?last_n_days=14"),
      api("/anomalies?max_results=30"),
      api("/daily?last_n_days=30"),
      api("/profile"),
      api("/forecast").catch(() => null),
    ])
      .then(([cost, usage, anomalies, daily, profile, forecast]) => {
        setCurrency(cost.currency);
        setD({
          cost,
          usage,
          anomalies,
          daily,
          profile,
          forecast,
        });
      })
      .catch((e) => {
        if (e.status === 409) {
          setEmpty(true);
        } else {
          setErr(true);
        }
      });

    loadEntries();
  }, []);

  if (empty) {
    return (
      <main>
        <header>
          <span className="mark">GridSense</span>

          <span className="hright">
            <span className="muted">{user?.email}</span>

            <button className="chip" onClick={onOpenSettings}>
              Settings
            </button>

            <button className="chip" onClick={onLogout}>
              Log out
            </button>
          </span>
        </header>

        <h1>No energy data in your account yet.</h1>

        <p className="lede">
          Load sample data to see how GridSense works. It is saved in your
          account only.
        </p>

        <button onClick={seed} disabled={seeding}>
          {seeding ? "Loading…" : "Load sample data"}
        </button>
      </main>
    );
  }

  if (err) {
    return (
      <main>
        <h1>GridSense</h1>

        <p>
          Can’t reach the API at {API}. Start it with{" "}
          <code>uvicorn main:app --reload</code> in the backend folder.
        </p>
      </main>
    );
  }

   if (!d) return <Splash message="Loading your energy data…" />;

  const { cost: c } = d;

  const costShare = Math.round(
    (c.generator_cost / c.total_cost) * 100
  );

  const maxRate = Math.max(
    c.grid_rate_per_kwh,
    c.generator_rate_per_kwh
  );

  return (
    <main>
      <header>
        <span className="mark">GridSense</span>

        <span className="hright">
          <span className="muted">
            {user?.email} · {entries.length} reading{entries.length === 1 ? "" : "s"}
          </span>

          <NavButtons />

          <button className="chip" onClick={onOpenSettings}>
            Settings
          </button>

          <button className="chip" onClick={onLogout}>
            Log out
          </button>
        </span>
      </header>

      <h1>
        The generator gave you {Math.round(c.generator_share_pct)}% of your
        power and {costShare}% of your bill.
      </h1>

      <p className="lede">
        {naira(c.total_cost)} over {c.days} days. The grid was off for{" "}
        {c.outage_pct}% of all hours. At this pace you’ll spend{" "}
        {naira(c.projected_30_day_cost)} in the next 30 days.
      </p>

      <Timeline usage={d.usage} anomalies={d.anomalies} />

      <div className="rates">
        <div>
          <span>Grid, per kWh</span>
          <div className="bar">
            <i
              style={{
                width: `${(c.grid_rate_per_kwh / maxRate) * 100}%`,
                background: "var(--grid)",
              }}
            />
          </div>
          <b>{naira(c.grid_rate_per_kwh)}</b>
        </div>

        <div>
          <span>Generator, per kWh</span>
          <div className="bar">
            <i
              style={{
                width: `${(c.generator_rate_per_kwh / maxRate) * 100}%`,
                background: "var(--gen)",
              }}
            />
          </div>
          <b>{naira(c.generator_rate_per_kwh)}</b>
        </div>
      </div>

      <AlertsPanel />
      <PlanPanel />
      <Reading onSaved={loadEntries} />
      <LoggedReadings entries={entries} />

      <Charts
        daily={d.daily}
        profile={d.profile}
        forecast={d.forecast}
      />

      <footer className="muted">
        Built with Meta’s Muse Spark. Demo data is simulated; change your
        tariff and generator costs in Settings.
      </footer>
    </main>
  );
}
