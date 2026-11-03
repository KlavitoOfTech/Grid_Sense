import { useEffect, useState } from "react";
import { api } from "./api";
import SettingsForm from "./SettingsForm";

export default function Settings({ user, welcome, onBack, onLogout }) {
  const [s, setS] = useState(null);
  const [err, setErr] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { api("/settings").then(setS).catch(() => setErr(true)); }, []);

  const onSaved = (data) => {
    try { sessionStorage.removeItem("gs-alerts"); } catch { /* ignore */ } // alerts depend on these numbers
    setS(data);
    setSaved(true);
    window.scrollTo(0, 0);
  };

  return (
    <main>
      <header>
        <span className="mark">GridSense</span>
        <span className="hright">
          <span className="muted">{user?.email}</span>
          <button className="chip" onClick={onLogout}>Log out</button>
        </span>
      </header>
      <h1>Your settings</h1>
      {welcome && !saved && <p className="lede">You’re all set. Here’s what we saved. GridSense now uses these numbers everywhere.</p>}
      {saved && <p className="lede">Saved. Your dashboard will use the new numbers.</p>}
      {err && <p className="muted">Couldn’t load your settings. Make sure the API is running.</p>}
      {!s && !err && <p className="muted">Loading…</p>}
      {s && <SettingsForm initial={s} onSaved={onSaved} />}
      <p><button className="chip" onClick={onBack}>← Back to dashboard</button></p>
    </main>
  );
}