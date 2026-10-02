// Shared formatting helpers (extracted from Logs.tsx / TestLab.tsx; behaviour unchanged).

export function fmt(ts: string) {
  try {
    return new Date(ts).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
  } catch {
    return ts;
  }
}

export function fmtTokens(n: unknown): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-IN") : "—";
}

export function fmtMs(ms: unknown): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

export function fmtCost(cost: unknown): string {
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "—";
  return `$${cost.toFixed(6)}`;
}

export const USD_TO_INR = 100;

/** Shared USD + INR cost display; "—" when missing. Keeps USD format, appends INR. */
export function fmtCostBoth(cost: unknown): string {
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "—";
  return `${fmtCost(cost)} (₹${(cost * USD_TO_INR).toFixed(2)})`;
}

/** Identifier-kind output shape: {selected_agents: string[]}. Null when not identifier. */
export function selectedAgentsOf(out: unknown): string[] | null {
  if (!out || typeof out !== "object" || Array.isArray(out)) return null;
  const v = (out as Record<string, unknown>).selected_agents;
  if (!Array.isArray(v)) return null;
  return v
    .filter((x): x is string => typeof x === "string")
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

/** Value cell text: "—" for null/undefined, compact JSON for objects, String() otherwise. */
export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") {
    try {
      return JSON.stringify(v);
    } catch {
      return String(v);
    }
  }
  return String(v);
}

export function serverDetail(e: unknown): string {
  if (typeof e === "object" && e !== null && "response" in e) {
    const resp = (e as { response?: { data?: { detail?: unknown } } }).response;
    if (resp && typeof resp.data === "object" && resp.data !== null && "detail" in resp.data) {
      const d = (resp.data as { detail?: unknown }).detail;
      if (typeof d === "string" && d.trim() !== "") return d;
    }
  }
  if (e instanceof Error && e.message) return e.message;
  return "Request failed";
}

/** Short model name: the part after the last "/". */
export function shortModel(id: string): string {
  const i = id.lastIndexOf("/");
  return i >= 0 ? id.slice(i + 1) : id;
}
