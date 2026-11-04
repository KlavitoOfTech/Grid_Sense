import { useState } from "react";
import { supabase } from "./supabase";

// Decorative sample of the power timeline (not real data)
const sample = (r, h) => {
  const x = (r * 13 + h * 7) % 11;
  return x < 2 ? "none" : x < 4 ? "generator" : "grid";
};

export function Landing({ onLogin, onSignup }) {
  return (
    <main>
      <header>
        <span className="mark">GridSense</span>
        <span className="hright">
          <button className="chip" onClick={onLogin}>Log in</button>
        </span>
      </header>

      <h1>Know where your power, and your money, goes.</h1>
      <p className="lede">
        GridSense turns your electricity data into plain advice for homes and small
        businesses that live with unreliable grid supply.
      </p>
      <div className="cta">
        <button onClick={onSignup}>Create a free account</button>
        <button className="chip" onClick={onLogin}>I already have an account</button>
      </div>

      <div className="timeline" aria-hidden="true" style={{ marginTop: "2.5rem" }}>
        {Array.from({ length: 7 }, (_, r) => (
          <div className="trow" key={r}>
            <span />
            {Array.from({ length: 24 }, (_, h) => <span key={h} className={`cell ${sample(r, h)}`} />)}
          </div>
        ))}
        <div className="legend">
          <span><i className="cell grid" /> Grid</span>
          <span><i className="cell generator" /> Generator</span>
          <span><i className="cell none" /> No power</span>
        </div>
      </div>

      <section>
        <h2>What it does</h2>
        <div className="features">
          <div><h3>Shows the real cost</h3><p>See how much of your bill comes from the grid and how much from your generator, hour by hour.</p></div>
          <div><h3>Warns you early</h3><p>Spots unusual use, like an appliance left on, before it becomes an expensive surprise.</p></div>
          <div><h3>Tells you what to do</h3><p>Ask questions in plain language, snap a photo of a meter or receipt, and get a savings plan.</p></div>
        </div>
      </section>
      <footer className="muted">Built with Meta’s Muse Spark.</footer>
    </main>
  );
}

export function AuthForm({ mode, setMode, onBack }) {
  const signup = mode === "signup";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showPw, setShowPw] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError(""); setNotice("");
    if (signup && password.length < 6) { setError("Use a password with at least 6 characters."); return; }
    setBusy(true);
    try {
      if (signup) {
        const { data, error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        // No session means the project requires email confirmation first
        if (!data.session) setNotice("Account created. Check your email for a confirmation link, then log in.");
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      }
      // On success the session listener in App moves the user to the dashboard
    } catch (err) {
      setError(err.message || "Something went wrong. Please try again.");
    }
    setBusy(false);
  };

  return (
    <main className="authbox">
      <header>
        <span className="mark">GridSense</span>
        <button className="chip" onClick={onBack}>Back</button>
      </header>
      <h2 style={{ marginTop: "2rem" }}>{signup ? "Create your account" : "Log in"}</h2>
      <form onSubmit={submit}>
        <label>Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            autoComplete="email" required />
        </label>
        <div className="field">
          <label htmlFor="password">Password</label>
          <div className="pwfield">
            <input id="password" type={showPw ? "text" : "password"} value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={signup ? "new-password" : "current-password"} required />
            <button type="button" className="pwtoggle" onClick={() => setShowPw((v) => !v)}
              aria-label={showPw ? "Hide password" : "Show password"} aria-pressed={showPw}>
              {showPw ? "Hide" : "Show"}
            </button>
          </div>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {notice && <p className="notice" role="status">{notice}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Please wait…" : signup ? "Create account" : "Log in"}
        </button>
      </form>
      <p className="muted">
        {signup ? "Already have an account? " : "New to GridSense? "}
        <button className="linkbtn" onClick={() => setMode(signup ? "login" : "signup")}>
          {signup ? "Log in" : "Create an account"}
        </button>
      </p>
    </main>
  );
}
