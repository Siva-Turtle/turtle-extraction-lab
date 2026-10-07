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

/** INR display for a USD cost ("—" when missing). USD value goes in `title`. */
export function fmtINR(costUsd: unknown): string {
  if (typeof costUsd !== "number" || !Number.isFinite(costUsd)) return "—";
  return `₹${(costUsd * USD_TO_INR).toFixed(2)}`;
}

/** Compact input-token display, e.g. 12_500 -> "~13k". */
export function fmtTokensShort(n: unknown): string {
  if (typeof n !== "number" || !Number.isFinite(n)) return "—";
  if (n >= 1000) return `~${Math.round(n / 1000)}k`;
  return `~${Math.round(n)}`;
}

/** Shared USD + INR cost display; "—" when missing. Keeps USD format, appends INR. */
export function fmtCostBoth(cost: unknown): string {
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "—";
  return `${fmtCost(cost)} (₹${(cost * USD_TO_INR).toFixed(2)})`;
}

/** Identifier-kind output shape (old): {selected_agents: string[]}. Null when not old. */
export function selectedAgentsOf(out: unknown): string[] | null {
  if (!out || typeof out !== "object" || Array.isArray(out)) return null;
  const v = (out as Record<string, unknown>).selected_agents;
  if (!Array.isArray(v)) return null;
  return v
    .filter((x): x is string => typeof x === "string")
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

/** Fixed 12 identifier question keys (v2 routing). */
export const IDENTIFIER_QUESTION_KEYS = [
  "has_assets",
  "has_accounts",
  "credit_cards",
  "employment_changed",
  "employment_status_changed",
  "alumni",
  "expenses",
  "goals",
  "income",
  "insurance",
  "liabilities",
  "tax",
] as const;

/** New identifier answers: {key: bool}. Null when not a v2 answer object. */
export function identifierAnswersOf(out: unknown): Record<string, boolean> | null {
  if (!out || typeof out !== "object" || Array.isArray(out)) return null;
  const rec = out as Record<string, unknown>;
  if ("selected_agents" in rec || "fillable_attributes" in rec) return null;
  if ("_error" in rec) return null;
  let found = false;
  const ans: Record<string, boolean> = {};
  for (const k of IDENTIFIER_QUESTION_KEYS) {
    const v = rec[k];
    if (typeof v === "boolean") {
      ans[k] = v;
      found = true;
    } else if (typeof v === "string") {
      const s = v.trim().toLowerCase();
      if (s === "true") {
        ans[k] = true;
        found = true;
      } else if (s === "false") {
        ans[k] = false;
        found = true;
      }
    }
  }
  if (!found) return null;
  // Fill missing keys as false for stable rendering.
  for (const k of IDENTIFIER_QUESTION_KEYS) {
    if (!(k in ans)) ans[k] = false;
  }
  return ans;
}

/**
 * Identifier fillable attributes: {fillable_attributes: [{agent, attributes}]}.
 * Returns a map agent -> attribute names. Empty object when missing/malformed.
 */
export function fillableAttributesOf(out: unknown): Record<string, string[]> {
  if (!out || typeof out !== "object" || Array.isArray(out)) return {};
  const v = (out as Record<string, unknown>).fillable_attributes;
  if (!Array.isArray(v)) return {};
  const map: Record<string, string[]> = {};
  for (const entry of v) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) continue;
    const rec = entry as Record<string, unknown>;
    const agent = rec.agent;
    if (typeof agent !== "string") continue;
    const name = agent.trim();
    if (name === "") continue;
    const attrs = rec.attributes;
    if (!Array.isArray(attrs)) continue;
    const names = attrs
      .filter((x): x is string => typeof x === "string")
      .map((s) => s.trim())
      .filter((s) => s !== "");
    if (map[name]) {
      for (const n of names) {
        if (!map[name].includes(n)) map[name].push(n);
      }
    } else {
      map[name] = names;
    }
  }
  return map;
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

/** Model label with reasoning effort: `short:effort`, or just short when effort is blank. */
export function modelLabel(model: string, effort: string, provider?: string): string {
  const short = shortModel(model);
  const e = (effort ?? "").trim();
  const base = e === "" ? short : `${short}:${e}`;
  if (provider === undefined) return base;
  const p = (provider ?? "").trim();
  return p === "" ? `${base} · Auto` : `${base} · ${p}`;
}

/** Requested-provider display ("Auto" when blank). */
export function providerDisplay(provider: string | null | undefined): string {
  const p = (provider ?? "").trim();
  return p === "" ? "Auto" : p;
}

/** Full slot label: `short · effort · provider` (effort omitted when blank). */
export function modelSlotLabel(model: string, effort: string, provider: string): string {
  const short = shortModel(model);
  const e = (effort ?? "").trim();
  const p = providerDisplay(provider);
  return e === "" ? `${short} · ${p}` : `${short} · ${e} · ${p}`;
}

/** Slot identity key (model + effort + provider) for compare columns. */
export function slotKeyOf(model: string, effort: string, provider: string): string {
  return `${model}|${effort}|${provider ?? ""}`;
}

/** Relative run time, e.g. "just now", "5 min ago", "3 h ago", "2 d ago". Falls back to fmt(). */
export function fmtRelative(ts: string): string {
  const t = new Date(ts).getTime();
  if (Number.isNaN(t)) return fmt(ts);
  const diffMs = Date.now() - t;
  if (diffMs < 0) return fmt(ts);
  const s = Math.floor(diffMs / 1000);
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} d ago`;
  return fmt(ts);
}
