// Phase 4 security test: profiles and energy_settings stay private, and bad data is rejected.
// Run from the frontend folder (Node 20.6+), same env vars as rls_check.mjs.
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

// A saves onboarding data (fuel mode)
const aSet = await a.client.from("energy_settings").upsert({
  user_id: a.id, currency: "NGN", grid_tariff: 250, generator_cost_mode: "fuel",
  generator_rate: null, fuel_price_per_litre: 1100, gen_litres_per_kwh: 0.7,
}).select().single();
check("A can save own energy settings", !aSet.error && aSet.data?.user_id === a.id);

const aProf = await a.client.from("profiles").update({
  account_name: "A Home", user_type: "household", city: "Lagos", state: "Lagos", onboarding_completed: true,
}).eq("id", a.id).select();
check("A can update own profile", !aProf.error && aProf.data?.length === 1);

// B tries to reach A's data
const bReadSet = await b.client.from("energy_settings").select("*").eq("user_id", a.id);
check("B cannot read A's settings", !bReadSet.error && bReadSet.data.length === 0);

const bReadProf = await b.client.from("profiles").select("*").eq("id", a.id);
check("B cannot read A's profile", !bReadProf.error && bReadProf.data.length === 0);

const bUpdProf = await b.client.from("profiles").update({ city: "Hacked" }).eq("id", a.id).select();
check("B cannot update A's profile", !bUpdProf.error && bUpdProf.data.length === 0);

const bUpdSet = await b.client.from("energy_settings").update({ grid_tariff: 1 }).eq("user_id", a.id).select();
check("B cannot update A's settings", !bUpdSet.error && bUpdSet.data.length === 0);

const bWriteAsA = await b.client.from("energy_settings").upsert({
  user_id: a.id, grid_tariff: 1, generator_cost_mode: "per_kwh", generator_rate: 1,
});
check("B cannot write settings as A", !!bWriteAsA.error);

// Bad data is rejected by the database
const badFuel = await b.client.from("energy_settings").upsert({
  user_id: b.id, grid_tariff: 100, generator_cost_mode: "fuel",
  generator_rate: null, fuel_price_per_litre: null, gen_litres_per_kwh: null,
});
check("fuel mode without fuel values is rejected", !!badFuel.error);

const badType = await b.client.from("profiles").update({ user_type: "alien" }).eq("id", b.id).select();
check("invalid user_type is rejected", !!badType.error);

const badCur = await b.client.from("energy_settings").upsert({
  user_id: b.id, currency: "naira", grid_tariff: 100, generator_cost_mode: "per_kwh", generator_rate: 500,
});
check("invalid currency code is rejected", !!badCur.error);

// A's data is intact
const aAfter = await a.client.from("profiles").select("city").eq("id", a.id).single();
check("A's profile is untouched", aAfter.data?.city === "Lagos");
const aSetAfter = await a.client.from("energy_settings").select("grid_tariff").eq("user_id", a.id).single();
check("A's settings are untouched", Number(aSetAfter.data?.grid_tariff) === 250);

console.log(failed ? `\n${failed} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(failed ? 1 : 0);