import { useEffect, useState } from "react";
import { supabase } from "./supabase";
import { Landing, AuthForm } from "./Public";
import Dashboard from "./Dashboard";

export default function App() {
  const [session, setSession] = useState(null);
  const [ready, setReady] = useState(false);
  const [view, setView] = useState("landing"); // landing | login | signup

  useEffect(() => {
    if (!supabase) { setReady(true); return; }
    supabase.auth.getSession().then(({ data }) => { setSession(data.session); setReady(true); });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, s) => setSession(s));
    return () => sub.subscription.unsubscribe();
  }, []);

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

  if (session) return <Dashboard user={session.user} onLogout={logout} />;
  if (view === "login" || view === "signup") {
    return <AuthForm mode={view} setMode={setView} onBack={() => setView("landing")} />;
  }
  return <Landing onLogin={() => setView("login")} onSignup={() => setView("signup")} />;
}
