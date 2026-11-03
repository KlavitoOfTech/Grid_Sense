import { supabase } from "./supabase";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";

// Every request carries the user's Supabase access token; the backend verifies it.
export async function api(path, options = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  const res = await fetch(API + path, {
    ...options,
    headers: { ...(options.headers || {}), Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) { await supabase.auth.signOut(); throw new Error("Session expired"); }
  if (!res.ok) {
    let detail = "";
    try {
      const b = await res.json();
      detail = Array.isArray(b.detail) ? b.detail.map((d) => d.msg.replace(/^Value error, /, "")).join("; ") : b.detail || "";
    } catch { /* no JSON body */ }
    const e = new Error(detail || `${path} failed (${res.status})`);
    e.status = res.status;
    throw e;
  }
  return res.json();
}

export const put = (path, body) =>
  api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });