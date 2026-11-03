// Shared currency formatting. The dashboard sets the user's currency once their data loads.
let current = "NGN";

export const CURRENCIES = [
  ["NGN", "Naira (₦)"],
  ["USD", "US dollar ($)"],
  ["GBP", "Pound (£)"],
  ["EUR", "Euro (€)"],
];

export const setCurrency = (c) => { current = c || "NGN"; };

export function money(n, currency = current) {
  const v = Math.round(n ?? 0);
  try {
    return new Intl.NumberFormat("en-NG", { style: "currency", currency, maximumFractionDigits: 0 }).format(v);
  } catch {
    return `${currency} ${v.toLocaleString("en-NG")}`;
  }
}