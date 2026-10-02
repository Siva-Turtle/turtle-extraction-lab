// Multi-model comparison helpers (pure functions, no React).
//
// Worked examples:
// - canonicalKey("  Hello   World ") === canonicalKey("hello world")
//   (strings: trimmed, inner whitespace collapsed, lowercased).
// - canonicalKey(["b", "a"]) === canonicalKey(["a", "B"])
//   (arrays canonicalised element-wise, then sorted, then JSON).
// - canonicalKey({ b: 1, a: 2 }) === canonicalKey({ a: 2, b: 1 })
//   (objects: keys sorted before JSON).
// - canonicalKey(1) === canonicalKey("1") (loose: numbers stringified).
// - canonicalKey(null) === "∅"; canonicalKey("") === "∅";
//   canonicalKey("x", "not_found") === "∅" (missing / not-found sentinel).
// - Identifier `selected_agents` ["a", "b"] vs ["b", "a"] agree (set semantics
//   via the sorted-array rule above).
// - buildRows over two done columns sharing one value -> state "unanimous"
//   with consensusKey set; every compared cell missing -> "none_found";
//   2-vs-1 split across three columns -> "majority"; 1-vs-1 -> "split";
//   no comparable column yet -> "pending".
// - columnStats counts thumbs from column.log.feedback[agentName][attr];
//   agreePct is 0-100 (% of the column's compared rows sitting in the row's
//   largest group, ties count).
// - linkedTargets returns the other columns sharing the cell's canonical
//   value (same groups entry, minus the cell itself).
// - groupAgreementPct = mean over compared rows of
//   (largest group size / compared count), 0-100; rows with nothing to
//   compare are skipped.

import type { CompareAgent, CompareColumn, LogRow } from "./logTypes";

export type CellState = {
  value: unknown;
  confidence?: unknown;
  confidenceType?: unknown;
  evidence?: unknown;
  canon: string;
  error?: string;
  feedback: { rating: string; remarks: string } | null;
};

export type CompareRow = {
  agentId: string;
  agentName: string;
  /** Attribute name (see CompareAgent.attributes for type/description). */
  attr: string;
  cells: Record<string, CellState>;
  /** Canonical value -> column keys holding it (compared cells only). */
  groups: Map<string, string[]>;
  state: "unanimous" | "majority" | "split" | "none_found" | "pending";
  consensusKey?: string;
};

/** Canonical key for cells that carry no comparable value. */
const PENDING_CANON = "__pending__";
const ERROR_CANON = "__error__";

/** Missing-value sentinel shared with the matrix UI ("— not found"). */
export const NOT_FOUND_CANON = "∅";

/** Recursively normalise a nested value the same loose way top-level values compare. */
function normDeep(v: unknown): unknown {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v.trim().replace(/\s+/g, " ").toLowerCase();
  if (typeof v === "number") return Number.isNaN(v) ? null : v;
  if (typeof v === "boolean") return v;
  if (Array.isArray(v)) {
    const mapped = v.map(normDeep);
    mapped.sort((a, b) => {
      const sa = JSON.stringify(a) ?? "";
      const sb = JSON.stringify(b) ?? "";
      return sa < sb ? -1 : sa > sb ? 1 : 0;
    });
    return mapped;
  }
  if (typeof v === "object") {
    const rec = v as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(rec).sort()) out[k] = normDeep(rec[k]);
    return out;
  }
  return String(v);
}

/**
 * Loose canonical key for one cell value. "∅" when missing
 * (null/undefined/"") or when the confidence type is `not_found`.
 */
export function canonicalKey(value: unknown, confidenceType?: unknown): string {
  if (confidenceType === "not_found") return NOT_FOUND_CANON;
  if (value === null || value === undefined) return NOT_FOUND_CANON;
  if (typeof value === "string") {
    const t = value.trim().replace(/\s+/g, " ").toLowerCase();
    return t === "" ? NOT_FOUND_CANON : t;
  }
  if (typeof value === "number") {
    if (Number.isNaN(value)) return NOT_FOUND_CANON;
    return String(Number(value));
  }
  if (typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(normDeep(value)) ?? NOT_FOUND_CANON;
  } catch {
    return String(value);
  }
}

/** Display name for an agent id within one log (mirrors SingleModelTable). */
function columnAgentName(log: LogRow, agentId: string, fallback: string): string {
  try {
    const snap = (log.agent_snapshot ?? {})[agentId];
    if (snap && typeof snap === "object" && typeof snap.name === "string") {
      const n = snap.name.trim();
      if (n !== "") return n;
    }
  } catch {
    // fall through to the fallback name
  }
  const f = (fallback ?? "").trim();
  return f !== "" ? f : agentId;
}

/** Saved feedback for one agent/attribute (mirrors SingleModelTable). Never throws. */
function cellFeedback(
  log: LogRow,
  agentName: string,
  attr: string,
): { rating: string; remarks: string } | null {
  try {
    const fb = log.feedback;
    if (!fb || typeof fb !== "object") return null;
    const byAgent = (fb as Record<string, unknown>)[agentName];
    if (!byAgent || typeof byAgent !== "object") return null;
    const entry = (byAgent as Record<string, unknown>)[attr];
    if (!entry || typeof entry !== "object") return null;
    const r = (entry as { rating?: unknown }).rating;
    const m = (entry as { remarks?: unknown }).remarks;
    return {
      rating: typeof r === "string" ? r : "",
      remarks: typeof m === "string" ? m : "",
    };
  } catch {
    return null;
  }
}

function agentOutput(
  log: LogRow,
  agentId: string,
  agentName: string,
): unknown {
  const outs = (log.outputs ?? {}) as Record<string, unknown>;
  const byId = outs[agentId];
  if (byId !== undefined && byId !== null) return byId;
  return outs[agentName];
}

/** Split one agent output object into the cell for one attribute. */
function extractCell(
  out: unknown,
  agentKind: string,
  attr: string,
): { value: unknown; confidence?: unknown; confidenceType?: unknown; evidence?: unknown } {
  if (!out || typeof out !== "object" || Array.isArray(out)) return { value: undefined };
  const rec = out as Record<string, unknown>;
  // Identifier agents report { selected_agents: string[] } as a whole, not
  // per-attribute { value, ... } objects — compare the set directly.
  if (agentKind === "identifier" && attr === "selected_agents" && !("value" in rec)) {
    return { value: rec.selected_agents };
  }
  const entry = rec[attr];
  if (
    entry &&
    typeof entry === "object" &&
    !Array.isArray(entry) &&
    "value" in (entry as Record<string, unknown>)
  ) {
    const e = entry as Record<string, unknown>;
    const ct = e.confidence_type ?? e.confidenceType;
    return { value: e.value, confidence: e.confidence, confidenceType: ct, evidence: e.evidence };
  }
  return { value: entry };
}

function hasAgentError(out: unknown): string | null {
  if (out && typeof out === "object" && !Array.isArray(out) && "_error" in out) {
    return String((out as Record<string, unknown>)._error);
  }
  return null;
}

/**
 * One row per agent × attribute. Attribute order follows the agent snapshot,
 * then any extra output keys seen in the compared columns (first-seen order).
 * Only done/partial columns whose agent has no `_error` join the comparison;
 * the rest get pending/error cells and are excluded from `groups`.
 */
export function buildRows(agents: CompareAgent[], columns: CompareColumn[]): CompareRow[] {
  const rows: CompareRow[] = [];
  for (const agent of agents) {
    const attrNames = agent.attributes.map((a) => a.name);
    const seen = new Set(attrNames);
    const extras: string[] = [];
    for (const col of columns) {
      if (!col.log || (col.status !== "done" && col.status !== "partial")) continue;
      const out = agentOutput(col.log, agent.id, agent.name);
      if (!out || typeof out !== "object" || Array.isArray(out)) continue;
      if (hasAgentError(out) !== null) continue;
      for (const k of Object.keys(out as Record<string, unknown>)) {
        if (k === "_error" || seen.has(k)) continue;
        seen.add(k);
        extras.push(k);
      }
    }
    for (const attr of [...attrNames, ...extras]) {
      const cells: Record<string, CellState> = {};
      for (const col of columns) {
        const log = col.log;
        if (!log || (col.status !== "done" && col.status !== "partial")) {
          cells[col.key] = {
            value: undefined,
            canon: PENDING_CANON,
            error: col.error,
            feedback: null,
          };
          continue;
        }
        const out = agentOutput(log, agent.id, agent.name);
        const agentErr = hasAgentError(out);
        if (agentErr !== null) {
          cells[col.key] = { value: undefined, canon: ERROR_CANON, error: agentErr, feedback: null };
          continue;
        }
        const part = extractCell(out, agent.kind, attr);
        const canon = canonicalKey(
          part.value,
          typeof part.confidenceType === "string" ? part.confidenceType : undefined,
        );
        const lookupName = columnAgentName(log, agent.id, agent.name);
        cells[col.key] = {
          value: part.value,
          confidence: part.confidence,
          confidenceType: part.confidenceType,
          evidence: part.evidence,
          canon,
          feedback: cellFeedback(log, lookupName, attr),
        };
      }
      const groups = new Map<string, string[]>();
      for (const col of columns) {
        const canon = cells[col.key]?.canon;
        if (!canon || canon === PENDING_CANON || canon === ERROR_CANON) continue;
        const list = groups.get(canon);
        if (list) list.push(col.key);
        else groups.set(canon, [col.key]);
      }
      let compared = 0;
      let largest = 0;
      let largestKey = "";
      for (const [k, keys] of groups) {
        compared += keys.length;
        if (keys.length > largest) {
          largest = keys.length;
          largestKey = k;
        }
      }
      let state: CompareRow["state"];
      let consensusKey: string | undefined;
      if (compared === 0) {
        state = "pending";
      } else if (groups.size === 1) {
        if (largestKey === NOT_FOUND_CANON) {
          state = "none_found";
        } else {
          state = "unanimous";
          consensusKey = largestKey;
        }
      } else if (largest > compared / 2) {
        state = "majority";
        consensusKey = largestKey;
      } else {
        state = "split";
      }
      rows.push({
        agentId: agent.id,
        agentName: agent.name,
        attr,
        cells,
        groups,
        state,
        ...(consensusKey !== undefined ? { consensusKey } : {}),
      });
    }
  }
  return rows;
}

/** Per-column tally: agreement % (0-100), thumbs counts, rated/total rows. */
export function columnStats(
  rows: CompareRow[],
  colKey: string,
): { agreePct: number; up: number; down: number; rated: number; total: number } {
  let up = 0;
  let down = 0;
  for (const r of rows) {
    const rating = r.cells[colKey]?.feedback?.rating;
    if (rating === "up") up += 1;
    else if (rating === "down") down += 1;
  }
  let numer = 0;
  let denom = 0;
  for (const r of rows) {
    const cell = r.cells[colKey];
    if (!cell || cell.canon === PENDING_CANON || cell.canon === ERROR_CANON) continue;
    let compared = 0;
    let largest = 0;
    for (const keys of r.groups.values()) {
      compared += keys.length;
      if (keys.length > largest) largest = keys.length;
    }
    if (compared === 0) continue;
    denom += 1;
    const group = r.groups.get(cell.canon);
    if (group && group.length === largest) numer += 1;
  }
  return {
    agreePct: denom === 0 ? 0 : (numer / denom) * 100,
    up,
    down,
    rated: up + down,
    total: rows.length,
  };
}

/** Other columns holding the same canonical value in this row. */
export function linkedTargets(_rows: CompareRow[], row: CompareRow, colKey: string): string[] {
  const cell = row.cells[colKey];
  if (!cell || cell.canon === PENDING_CANON || cell.canon === ERROR_CANON) return [];
  const group = row.groups.get(cell.canon);
  if (!group) return [];
  return group.filter((k) => k !== colKey);
}

/**
 * Group agreement % (0-100): mean over compared rows of
 * (largest group size / compared count). Rows with nothing to compare
 * (pending everywhere) are skipped; 0 when there are none.
 */
export function groupAgreementPct(rows: CompareRow[]): number {
  let sum = 0;
  let n = 0;
  for (const r of rows) {
    let compared = 0;
    let largest = 0;
    for (const keys of r.groups.values()) {
      compared += keys.length;
      if (keys.length > largest) largest = keys.length;
    }
    if (compared === 0) continue;
    sum += largest / compared;
    n += 1;
  }
  return n === 0 ? 0 : (sum / n) * 100;
}
