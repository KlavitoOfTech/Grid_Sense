import { useEffect, useState } from "react";
import { api } from "./api";
import { money, setCurrency } from "./money";

const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

/* ---------- tiny router: the URL decides the page ---------- */
export function usePath() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return path;
}

export function go(path) {
  if (window.location.pathname !== path) window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/* ---------- small shared pieces ---------- */
const when = (iso) =>
  iso ? new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) : "";

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
      Checked: {[...new Set(used)].map((t) => t.replace("get_", "").replaceAll("_", " ")).join(", ")}
    </p>
  );
}

function NoData() {
  return (
    <p className="muted">
      No energy data yet.{" "}
      <button className="chip" onClick={() => go("/")}>Go to the dashboard</button> to load sample data.
    </p>
  );
}

export function NavButtons() {
  const path = usePath();
  return (
    <>
        {[["/", "Dashboard"], ["/alerts", "Alerts"], ["/savings", "Savings"], ["/forecast", "Forecast"], ["/advisor", "Advisor"]].map(
        ([to, label]) => (
          <button key={to} className="chip" disabled={path === to} onClick={() => go(to)}>
            {label}
          </button>
        )
      )}
    </>
  );
}

function Shell({ user, onLogout, onOpenSettings, title, children }) {
  return (
    <main>
      <header>
        <span className="mark">GridSense</span>
        <span className="hright">
          <span className="muted">{user?.email}</span>
          <NavButtons />
          {onOpenSettings && (
            <button className="chip" onClick={onOpenSettings}>Settings</button>
          )}
          <button className="chip" onClick={onLogout}>Log out</button>
        </span>
      </header>
      <h1>{title}</h1>
      {children}
    </main>
  );
}

/* ---------- alerts: saved results, refreshed only when data changed or on request ---------- */
let alertsInflight = null; // so two components (or React dev double-mount) never start two runs
const runAlerts = () => {
  if (!alertsInflight) {
    alertsInflight = post("/alerts/refresh").finally(() => { alertsInflight = null; });
  }
  return alertsInflight;
};

function useAlerts() {
  const [data, setData] = useState(null);
  const [state, setState] = useState("loading"); // loading | ready | refreshing | nodata | error
  const [err, setErr] = useState("");

  const fail = (e) => { setErr(e.message); setState(e.status === 409 ? "nodata" : "error"); };

  const refresh = async () => {
    setState("refreshing");
    setErr("");
    try { setData(await runAlerts()); setState("ready"); } catch (e) { fail(e); }
  };

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const d = await api("/alerts/latest"); // reads saved alerts, never calls the AI
        if (!live) return;
        setData(d);
        if (d.stale) { // no run yet, or readings changed since the last run: refresh once
          setState("refreshing");
          const fresh = await runAlerts();
          if (!live) return;
          setData(fresh);
        }
        setState("ready");
      } catch (e) {
        if (live) fail(e);
      }
    })();
    return () => { live = false; };
  }, []);

  return { data, state, err, refresh };
}

export function AlertsPanel({ full = false }) {
  const { data, state, err, refresh } = useAlerts();
  const [older, setOlder] = useState([]);
  const stamp = data?.created_at;
  const count = data?.alerts?.length ?? 0;

  useEffect(() => {
    if (!full || !stamp) return;
    let live = true;
    api("/alerts/history")
      .then((h) => { if (live) setOlder(h.slice(count)); })
      .catch(() => {});
    return () => { live = false; };
  }, [full, stamp, count]);

  const busy = state === "loading" || state === "refreshing" || state === "nodata";

  return (
    <section>
      <h2>Needs your attention <Badge mode={data?.mode} /></h2>

      {stamp && <p className="muted">Last checked {when(stamp)}</p>}
      {state === "loading" && <p className="muted">Loading your saved alerts…</p>}
      {state === "refreshing" && <p className="muted">Checking your latest usage…</p>}
      {state === "nodata" && <NoData />}
      {state === "error" && <p className="muted">Couldn’t run the check: {err}</p>}

      {data?.alerts?.map((a) => (
        <div className={`alert ${a.severity}`} key={a.id}>
          <strong>{a.title}</strong>
          <p>{a.what_happened}</p>
          {a.likely_cause && <p><span className="k">Likely cause</span> {a.likely_cause}</p>}
          {a.action && <p><span className="k">Do this</span> {a.action}</p>}
        </div>
      ))}

      {state === "ready" && count === 0 && <p className="muted">No alerts saved yet.</p>}
      <Tools used={data?.tools_used} />

      <button onClick={refresh} disabled={busy}>Check again</button>{" "}
      {!full && <button className="chip" onClick={() => go("/alerts")}>All alerts</button>}

      {full && older.length > 0 && (
        <>
          <h3>Earlier alerts</h3>
          {older.map((a) => (
            <div className={`alert ${a.severity}`} key={a.id}>
              <strong>{a.title}</strong> <span className="muted">· {when(a.created_at)}</span>
              <p>{a.what_happened}</p>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

export function AlertsPage(props) {
  return (
    <Shell {...props} title="Alerts">
      <AlertsPanel full />
    </Shell>
  );
}

/* ---------- savings plans: built only when the user asks ---------- */
function usePlan() {
  const [data, setData] = useState(null);
  const [state, setState] = useState("loading"); // loading | ready | building | nodata | error
  const [err, setErr] = useState("");

  const fail = (e) => { setErr(e.message); setState(e.status === 409 ? "nodata" : "error"); };

  useEffect(() => {
    let live = true;
    api("/plans/latest")
      .then((d) => { if (live) { setData(d); setState("ready"); } })
      .catch((e) => { if (live) fail(e); });
    return () => { live = false; };
  }, []);

  const build = async () => {
    setState("building");
    setErr("");
    try { setData(await post("/plans/refresh")); setState("ready"); } catch (e) { fail(e); }
  };

  return { data, state, err, build };
}

function PlanBody({ p }) {
  return (
    <div className="plan">
      <h3>{p.headline}</h3>
      <p>{p.summary}</p>
      <ul className="actions">
        {p.actions?.map((a, i) => (
          <li key={i}>
            <div>
              <strong>{a.title}</strong>
              <span className="muted"> · {a.when}</span>
              <p>{a.do}</p>
              <p className="muted">{a.why}</p>
            </div>
            <div className="save">
              {money(a.est_saving_ngn_per_month)}
              <small>per month</small>
            </div>
          </li>
        ))}
      </ul>
      <p className="total">
        Estimated saving: <strong>{money(p.total_est_saving_ngn_per_month)}</strong> a month
      </p>
      {p.watch_out?.map((w, i) => <p className="muted" key={i}>Watch out: {w}</p>)}
    </div>
  );
}

export function PlanPanel() {
  const { data, state } = usePlan();
  const p = data?.plan;
  return (
    <section>
      <h2>Your savings plan <Badge mode={data?.mode} /></h2>
      {state === "loading" && <p className="muted">Loading your saved plan…</p>}
      {state !== "loading" && !p && (
        <p className="muted">No plan yet. Muse Spark studies your usage and writes one when you ask.</p>
      )}
      {p && (
        <>
          <h3>{p.headline}</h3>
          <p>{p.summary}</p>
          <p className="total">
            Estimated saving: <strong>{money(p.total_est_saving_ngn_per_month)}</strong> a month
            <span className="muted"> · saved {when(data.created_at)}</span>
          </p>
          {data.stale && <p className="muted">Your readings have changed since this plan was built.</p>}
        </>
      )}
      <button onClick={() => go("/savings")}>{p ? "Open plan" : "Build my plan"}</button>
    </section>
  );
}

export function SavingsPage(props) {
  const { data, state, err, build } = usePlan();
  const [older, setOlder] = useState([]);
  const [runs, setRuns] = useState([]);
  const p = data?.plan;
  const stamp = data?.created_at;
  const loaded = state !== "loading";

  useEffect(() => {
    if (!loaded) return;
    let live = true;
    api("/plans/history")
      .then((h) => { if (live) setOlder(h.slice(stamp ? 1 : 0)); })
      .catch(() => {});
    api("/analysis/history")
      .then((r) => {
        if (!live) return;
        const c = r.find((x) => x.summary?.costs)?.summary.costs;
        if (c?.currency) setCurrency(c.currency);
        setRuns(r);
      })
      .catch(() => {});
    return () => { live = false; };
  }, [loaded, stamp]);

  const busy = state === "loading" || state === "building" || state === "nodata";

  return (
    <Shell {...props} title="Savings plan">
      <section>
        {state === "loading" && <p className="muted">Loading your saved plan…</p>}
        {state === "nodata" && <NoData />}
        {state === "error" && <p className="muted">Couldn’t build the plan: {err}</p>}

        {p && data.stale && (
          <p className="muted">
            <strong>Your readings have changed since this plan was built.</strong> Rebuild it to include them.
          </p>
        )}

        {p && (
          <>
            <p className="muted">Saved {when(stamp)} <Badge mode={data.mode} /></p>
            <PlanBody p={p} />
            <Tools used={data.tools_used} />
          </>
        )}

        {state === "ready" && !p && (
          <p className="muted">
            No plan yet. Muse Spark studies your usage, tests what-if scenarios and writes one.
          </p>
        )}

        <button onClick={build} disabled={busy}>
          {state === "building" ? "Working it out… (this can take a minute)" : p ? "Rebuild plan" : "Build my plan"}
        </button>
      </section>

      {older.length > 0 && (
        <section>
          <h2>Earlier plans</h2>
          {older.map((r) => (
            <details key={r.id}>
              <summary>
                {when(r.created_at)} · {r.plan?.headline}
                {r.plan?.total_est_saving_ngn_per_month != null &&
                  ` · ${money(r.plan.total_est_saving_ngn_per_month)}/month`}
              </summary>
              {r.plan && <PlanBody p={r.plan} />}
            </details>
          ))}
        </section>
      )}

      {runs.length > 0 && (
        <section>
          <h2>Analysis history</h2>
          <p className="muted">A snapshot is saved each time alerts or a plan are generated.</p>
          <ul className="actions">
            {runs.map((r) => {
              const c = r.summary?.costs;
              return (
                <li key={r.id}>
                  <div>
                    <strong>{r.kind === "alerts" ? "Alerts check" : "Savings plan"}</strong>
                    <span className="muted"> · {when(r.created_at)}</span>
                    {c && (
                      <p className="muted">
                        {c.days} days · {money(c.total_cost)} total · generator {c.generator_share_pct}% of energy
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </Shell>
  );
}

/* ---------- advisor: chat with saved history ---------- */
export function AdvisorPage(props) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [res, setRes] = useState(null);
  const [hist, setHist] = useState(null);

  const load = () => api("/chat/history").then(setHist).catch(() => setHist([]));
  useEffect(() => { load(); }, []);

  const ask = async (text) => {
    if (!text.trim() || busy) return;
    setQ(text);
    setBusy(true);
    setErr("");
    try {
      const r = await post("/ask", { question: text });
      setRes({ question: text, ...r });
      setQ("");
      await load();
    } catch (e) {
      setErr(
        e.status === 409
          ? "No energy data yet. Load sample data on the dashboard first."
          : "Couldn’t get an answer. Make sure the API is running."
      );
    } finally {
      setBusy(false);
    }
  };

  const ideas = [
    "Why is my bill so high?",
    "When should I run the washing machine?",
    "What readings have I logged?",
  ];

  // the answer just given is shown in its own box, so skip its copy in the saved list
  const rows =
    hist && res && hist[0]?.question === res.question && hist[0]?.answer === res.answer
      ? hist.slice(1)
      : hist;

  return (
    <Shell {...props} title="Advisor">
      <section>
        <div className="chips">
          {ideas.map((s) => (
            <button key={s} className="chip" onClick={() => ask(s)} disabled={busy}>{s}</button>
          ))}
        </div>

        <div className="askrow">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Ask about your power use or costs"
            onKeyDown={(e) => e.key === "Enter" && ask(q)}
            aria-label="Your question"
          />
          <button onClick={() => ask(q)} disabled={busy}>{busy ? "Thinking…" : "Ask"}</button>
        </div>

        {err && <p className="muted">{err}</p>}

        {res && (
          <div className="answer">
            <p><strong>{res.question}</strong> <Badge mode={res.mode} /></p>
            <p>{res.answer}</p>
            <Tools used={res.tools_used} />
          </div>
        )}
      </section>

      <section>
        <h2>Saved conversations</h2>
        {hist === null && <p className="muted">Loading…</p>}
        {rows?.length === 0 && <p className="muted">Nothing saved yet. Ask a question above.</p>}
        {rows?.map((r) => (
          <div className="answer" key={r.id}>
            <p>
              <strong>{r.question}</strong> <span className="muted">· {when(r.created_at)}</span>{" "}
              <Badge mode={r.mode} />
            </p>
            <p>{r.answer}</p>
            <Tools used={r.tools_used} />
          </div>
        ))}
      </section>
    </Shell>
  );
}

/* ---------- forecast: simple baseline estimate from the user's own readings ---------- */
function useBaseline() {
  const [data, setData] = useState(null);
  const [state, setState] = useState("loading"); // loading | ready | nodata | short | error
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    api("/forecast/baseline")
      .then((d) => { if (live) { setCurrency(d.currency); setData(d); setState("ready"); } })
      .catch((e) => {
        if (!live) return;
        setErr(e.message);
        setState(e.status === 409 ? "nodata" : e.status === 422 ? "short" : "error");
      });
    return () => { live = false; };
  }, []);

  return { data, state, err };
}

const kwh = (n) => (Math.round(n * 10) / 10).toLocaleString("en-NG");

function BaselineBody({ d }) {
  return (
    <>
      <p className="muted">
        <strong>Simple estimate, not a trained model.</strong> Based on your last {d.days_used} complete
        days ({d.window_start} to {d.window_end}).
      </p>
      <p className="lede">
        About {kwh(d.predicted_daily_kwh)} kWh a day, {kwh(d.predicted_monthly_kwh)} kWh over the next 30 days.
      </p>
      <div className="rates">
        <div>
          <span>Expected monthly cost, at your recent grid/generator mix</span>
          <b>{money(d.monthly_cost_expected_mix)}</b>
          <p className="muted">
            {d.grid_share_pct}% grid at {money(d.grid_tariff_per_kwh)}/kWh, {d.generator_share_pct}% generator
            at {money(d.generator_rate_per_kwh)}/kWh
          </p>
        </div>
        <div>
          <span>If all of it came from the grid</span>
          <b>{money(d.monthly_cost_all_grid)}</b>
          <p className="muted">{kwh(d.predicted_monthly_kwh)} kWh × {money(d.grid_tariff_per_kwh)}/kWh</p>
        </div>
      </div>
      <p className="muted">
        Daily use in this window ranged from {kwh(d.daily_kwh_min)} to {kwh(d.daily_kwh_max)} kWh.
        {d.trend_vs_previous_window_pct != null &&
          ` That is ${d.trend_vs_previous_window_pct > 0 ? "up" : "down"} ${Math.abs(
            d.trend_vs_previous_window_pct
          )}% on the 7 days before.`}
      </p>
    </>
  );
}

function BaselineStates({ state, err }) {
  return (
    <>
      {state === "loading" && <p className="muted">Working out your estimate…</p>}
      {state === "nodata" && <NoData />}
      {state === "short" && <p className="muted">{err}</p>}
      {state === "error" && <p className="muted">Couldn’t build the estimate: {err}</p>}
    </>
  );
}

export function BaselinePanel() {
  const { data, state, err } = useBaseline();
  return (
    <section>
      <h2>Next 30 days (estimate)</h2>
      <BaselineStates state={state} err={err} />
      {data && <BaselineBody d={data} />}
      <button className="chip" onClick={() => go("/forecast")}>Forecast details</button>
    </section>
  );
}

export function ForecastPage(props) {
  const { data, state, err } = useBaseline();
  const [hourly, setHourly] = useState(undefined); // undefined = loading, null = unavailable

  useEffect(() => {
    let live = true;
    api("/forecast").then((f) => { if (live) setHourly(f); }).catch(() => { if (live) setHourly(null); });
    return () => { live = false; };
  }, []);

  return (
    <Shell {...props} title="Forecast">
      <section>
        <h2>Next 30 days (estimate)</h2>
        <BaselineStates state={state} err={err} />
        {data && <BaselineBody d={data} />}
        {data && <p className="muted">{data.method}</p>}
      </section>

      <section>
        <h2>Next 24 hours (hourly model)</h2>
        {hourly === undefined && <p className="muted">Loading…</p>}
        {hourly === null && <p className="muted">Not available yet. It needs at least 10 days of hourly readings.</p>}
        {hourly && (
          <>
            <p className="lede">
              About {hourly.next_24h_kwh} kWh, peaking at {hourly.peak_hour.timestamp.slice(11, 16)}.
            </p>
            <p className="muted">
              Method: {hourly.method === "gru" ? "PyTorch GRU" : "same-hour average of the last 7 days"}. Average error on{" "}
              {hourly.metrics.test_days} unseen days:{" "}
              {hourly.method === "gru" && hourly.metrics.gru_mae_kwh != null
                ? `${hourly.metrics.gru_mae_kwh} kWh per hour (${hourly.metrics.improvement_pct}% better than the baseline).`
                : `${hourly.metrics.baseline_mae_kwh} kWh per hour.`}
            </p>
          </>
        )}
      </section>
    </Shell>
  );
}