import { useEffect, useState } from "react";
import { supabase } from "./supabase";
import { api } from "./api";
import { Landing, AuthForm } from "./Public";
import Dashboard from "./Dashboard";
import Onboarding from "./Onboarding";
import Settings from "./Settings";

export default function App() {
  const [session, setSession] = useState(null);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState("landing"); // landing | login | signup
  const [gate, setGate] = useState("checking"); // checking | onboarding | done | error
  const [settings, setSettings] = useState(null);
  const [screen, setScreen] = useState("dashboard"); // dashboard | settings
  const [welcome, setWelcome] = useState(false);

  useEffect(() => {
    if (!supabase) { setReady(true); return; }
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setReady(true); });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

  // After login, ask the backend whether this user has finished onboarding.
  const checkGate = () => {
    setGate("checking");
    api("/settings")
      .then((s) => { setSettings(s); setGate(s.onboarding_completed ? "done" : "onboarding"); })
      .catch(() => setGate("error"));
  };
  const userId = session?.user?.id;
  useEffect(() => {
    if (userId) { checkGate(); return; }
    setGate("checking"); setSettings(null); setScreen("dashboard"); setWelcome(false);
  }, [userId]);

  const logout = async () => {
    await supabase.auth.signOut();
    try { sessionStorage.clear(); } catch { /* ignore */ }
    setView("landing");
  };

  if (!supabase) {
    return (
      <main>
        <h1>Setup needed</h1>
        <p>Add <code>VITE_SUPABASE_URL</code> and <code>VITE_SUPABASE_ANON_KEY</code> to <code>frontend/.env</code>, then restart <code>npm run dev</code>.</p>
      </main>
    );
  }
  if (!ready) return <main><p className="muted">Loading…</p></main>;

  if (session) {
    const user = session.user;
    if (gate === "checking") return <main><p className="muted">Loading…</p></main>;
    if (gate === "error") {
      return (
        <main>
          <h1>GridSense</h1>
          <p>Can’t reach the API. Start it with <code>uvicorn main:app --reload</code> in the backend folder.</p>
          <button onClick={checkGate}>Try again</button>
        </main>
      );
    }
    if (gate === "onboarding") {
      return (
        <Onboarding
          user={user}
          settings={settings}
          onLogout={logout}
          onDone={(s) => { setSettings(s); setGate("done"); setScreen("settings"); setWelcome(true); }}
        />
      );
    }
    if (screen === "settings") {
      return <Settings user={user} welcome={welcome} onLogout={logout}
        onBack={() => { setScreen("dashboard"); setWelcome(false); }} />;
    }
    return <Dashboard user={user} onLogout={logout} onOpenSettings={() => setScreen("settings")} />;
  }

  if (view === "login" || view === "signup") {
    return <AuthForm mode={view} setMode={setView} onBack={() => setView("landing")} />;
  }
  return <Landing onLogin={() => setView("login")} onSignup={() => setView("signup")} />;
}