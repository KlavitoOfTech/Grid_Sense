// Phase 2 completion test: two real accounts, one real API. Neither may touch the other's data.
//
// Run from the frontend folder (Node 20.6+):
//   A_EMAIL=a@test.com A_PASSWORD=secret1 B_EMAIL=b@test.com B_PASSWORD=secret2 \
//   node --env-file=.env rls_check.mjs
// (On Windows PowerShell set the variables first:  $env:A_EMAIL="a@test.com"; ...)

import { createClient } from "@supabase/supabase-js";

const url = process.env.VITE_SUPABASE_URL;
const key = process.env.VITE_SUPABASE_ANON_KEY;
if (!url || !key) throw new Error("Missing VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY (use --env-file=.env)");

async function login(email, password) {
  const client = createClient(url, key);
  const { data, error } = await client.auth.signInWithPassword({ email, password });
  if (error) throw new Error(`Login failed for ${email}: ${error.message}`);
  return { client, id: data.user.id };
}

let failed = 0;
const check = (name, ok) => { console.log(ok ? "PASS" : "FAIL", "-", name); if (!ok) failed++; };

const a = await login(process.env.A_EMAIL, process.env.A_PASSWORD);
const b = await login(process.env.B_EMAIL, process.env.B_PASSWORD);
check("the two accounts are different users", a.id !== b.id);

// A creates data; the user_id is filled in by the database, not the client
const ins = await a.client.from("energy_readings")
  .insert({ timestamp: new Date().toISOString(), demand_kwh: 1.5, source: "grid", cost: 314 })
  .select().single();
check("A can insert and own a reading", !ins.error && ins.data?.user_id === a.id);
const rowId = ins.data?.id;

const chat = await a.client.from("chat_history").insert({ question: "rls test", answer: "rls test" }).select().single();
check("A can insert chat history", !chat.error);

// B tries to get at A's data
const read = await b.client.from("energy_readings").select("*").eq("id", rowId);
check("B cannot read A's reading", !read.error && read.data.length === 0);

const all = await b.client.from("energy_readings").select("user_id");
check("B's own view contains only B's rows", !all.error && all.data.every((r) => r.user_id === b.id));

const chatRead = await b.client.from("chat_history").select("*").eq("question", "rls test");
check("B cannot read A's chat history", !chatRead.error && chatRead.data.length === 0);

const upd = await b.client.from("energy_readings").update({ demand_kwh: 99 }).eq("id", rowId).select();
check("B cannot update A's reading", !upd.error && upd.data.length === 0);

const del = await b.client.from("energy_readings").delete().eq("id", rowId).select();
check("B cannot delete A's reading", !del.error && del.data.length === 0);

const spoof = await b.client.from("energy_alerts").insert({ user_id: a.id, title: "spoofed alert" });
check("B cannot insert a row owned by A", !!spoof.error);

const mine = await b.client.from("energy_alerts").insert({ title: "B's own alert" }).select().single();
check("B can insert their own alert", !mine.error && mine.data?.user_id === b.id);
const steal = await b.client.from("energy_alerts").update({ user_id: a.id }).eq("id", mine.data?.id).select();
check("B cannot hand their row to A", !!steal.error || steal.data.length === 0);

const profiles = await b.client.from("profiles").select("id");
check("B sees only their own profile", !profiles.error && profiles.data.length === 1 && profiles.data[0].id === b.id);

// A's data is intact
const still = await a.client.from("energy_readings").select("demand_kwh").eq("id", rowId).single();
check("A's reading is untouched", Number(still.data?.demand_kwh) === 1.5);

// Not logged in at all
const anon = createClient(url, key);
const nobody = await anon.from("energy_readings").select("*");
check("a logged-out visitor sees nothing", !!nobody.error || nobody.data.length === 0);

// Clean up the test rows
await a.client.from("energy_readings").delete().eq("id", rowId);
await a.client.from("chat_history").delete().eq("question", "rls test");
await b.client.from("energy_alerts").delete().eq("title", "B's own alert");

console.log(failed === 0 ? "\nALL CHECKS PASSED: users are isolated." : `\n${failed} CHECK(S) FAILED: do not ship until fixed.`);
process.exit(failed === 0 ? 0 : 1);
