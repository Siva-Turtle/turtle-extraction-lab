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
// - similarity("hello world", "hello world") === 1; similarity("a", "b") === 0.
// - similarity(["a","b"], ["b","c"]) is Jaccard 1/3 ≈ 0.33 (normalised sets).
// - Long strings (> 6 words either side) use token-set Jaccard; numbers use a
//   1% tolerance; booleans/enums/short strings are exact after normalise.
// - rowAgreement = mean pairwise similarity (1 when <2 values); peerAgreement
//   = mean similarity of one column to the others.
// - Sample identifier log (4 models picking 7/8/9/8 agents sharing 6):
//   selected_agents rowAgreement ≈ 0.81 -> score ≈ 0.81, displayed "81%".
//   Exact grouping still shows 4 distinct groups (state "split"), but the
//   score captures the overlap; link-identical still uses exact groups.
// - buildRows over two done columns sharing one value -> state "unanimous"
//   with consensusKey set; every compared cell missing -> "none_found" and
//   allEmpty true; 2-vs-1 split across three columns -> "majority"; 1-vs-1 ->
//   "split"; no comparable column yet -> "pending" with score null.
// - Identifier agents (kind === "identifier") produce one row per candidate
//   agent name (attr = agent name, e.g. "basic_info"): the cell is that
//   column's fillable list (string[]; [] when selected with no list) or
//   missing when not selected. A "selected_agents" row is kept too.
// - columnStats.agreePct is 0-100 (mean peerAgreement over comparable rows,
//   excluding allEmpty and <2-compared rows); thumbs still count from
//   column.log.feedback[agentName][attr].
// - linkedTargets returns the other columns sharing the cell's canonical
//   value (same groups entry, minus the cell itself).
// - groupAgreementPct = mean row score over comparable rows (excluding
//   allEmpty and <2-compared), 0-100; emptyRowCount counts allEmpty rows.

import type { CompareAgent, CompareColumn, LogRow } from "./logTypes";
import { peerAgreement, rowAgreement } from "./similarity";

export type CellState = {
  value: unknown;
  confidence?: unknown;
  confidenceType?: unknown;
  evidence?: unknown;
  canon: string;
  error?: string;
  feedback: { rating: string; remarks: string; auto?: boolean } | null;
  /** True when the attribute returns its raw list (no wrapper). */
  unwrapped?: boolean;
};

/** True when an attribute is unwrapped (raw list). Uses the snapshot's
 * wrap_result; falls back to "value is a list and not a dict" detection. */
export function isUnwrappedAttr(
  attrMeta: { wrap_result?: boolean } | undefined,
  rawEntry: unknown,
): boolean {
  if (attrMeta && typeof attrMeta.wrap_result === "boolean") {
    return attrMeta.wrap_result === false;
  }
  return Array.isArray(rawEntry);
}

export type ElementDiff =
  | { items: { value: string; count: number }[]; total: number }
  | { keys: { key: string; agree: boolean }[] };

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
  /** Mean pairwise similarity over compared cells (null when <2 compared). */
  score: number | null;
  /** Every compared cell is missing (canon ∅). */
  allEmpty: boolean;
  elementDiff?: ElementDiff;
  /** Attribute type hint used for similarity (from CompareAgent.attributes). */
  attrType?: string;
};

/** Canonical key for cells that carry no comparable value. */
const PENDING_CANON = "__pending__";
const ERROR_CANON = "__error__";

/** Missing-value sentinel shared with the matrix UI ("— not found"). */
export const NOT_FOUND_CANON = "∅";

/**
 * Agent a model did not select (auto-select runs). Muted "not selected"
 * cells, excluded from agreement like pending/error.
 */
export const NOT_SELECTED_CANON = "__not_selected__";

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

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

/** True when a column has a log worth comparing: done/partial, or a
 * streaming provisional log (status running with a log). Queued/error
 * without a log stay pending. Exported for streaming placeholders. */
export function isLiveColumn(col: CompareColumn): boolean {
  if (!col.log) return false;
  return col.status === "done" || col.status === "partial" || col.status === "running";
}

/** True when the provisional/final snapshot lists this agent (streaming
 * start event seeds the snapshot, so missing output means still running,
 * not "not selected"). */
function snapshotHasAgent(log: LogRow, agentId: string): boolean {
  try {
    const snap = (log.agent_snapshot ?? {}) as Record<string, unknown>;
    return Object.prototype.hasOwnProperty.call(snap, agentId);
  } catch {
    return false;
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
): { rating: string; remarks: string; auto?: boolean } | null {
  try {
    const fb = log.feedback;
    if (!fb || typeof fb !== "object") return null;
    const byAgent = (fb as Record<string, unknown>)[agentName];
    if (!byAgent || typeof byAgent !== "object") return null;
    const entry = (byAgent as Record<string, unknown>)[attr];
    if (!entry || typeof entry !== "object") return null;
    const r = (entry as { rating?: unknown }).rating;
    const m = (entry as { remarks?: unknown }).remarks;
    const a = (entry as { auto?: unknown }).auto;
    return {
      rating: typeof r === "string" ? r : "",
      remarks: typeof m === "string" ? m : "",
      ...(a === true ? { auto: true as const } : {}),
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

/** True when the log ran this agent (output present by id or name). */
function hasAgentOutput(log: LogRow, agentId: string, agentName: string): boolean {
  try {
    const outs = (log.outputs ?? {}) as Record<string, unknown>;
    if (Object.prototype.hasOwnProperty.call(outs, agentId)) return true;
    if (agentName !== agentId && Object.prototype.hasOwnProperty.call(outs, agentName)) return true;
    return false;
  } catch {
    return false;
  }
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

function displayItem(v: unknown): string {
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (v === null || v === undefined) return "—";
  try {
    const s = JSON.stringify(v);
    return s ?? String(v);
  } catch {
    return String(v);
  }
}

function buildItemsDiff(
  compared: { cell: CellState }[],
): { items: { value: string; count: number }[]; total: number } {
  const total = compared.length;
  const counts = new Map<string, number>();
  const display = new Map<string, string>();
  for (const { cell } of compared) {
    if (cell.canon === NOT_FOUND_CANON) continue;
    if (!Array.isArray(cell.value)) continue;
    const seen = new Set<string>();
    for (const item of cell.value) {
      const k = canonicalKey(item);
      if (seen.has(k)) continue;
      seen.add(k);
      counts.set(k, (counts.get(k) ?? 0) + 1);
      if (!display.has(k)) display.set(k, displayItem(item));
    }
  }
  const items = [...counts.entries()].map(([k, count]) => ({
    value: display.get(k) ?? k,
    count,
  }));
  items.sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  return { items, total };
}

function buildKeysDiff(compared: { cell: CellState }[]): { keys: { key: string; agree: boolean }[] } {
  const union = new Set<string>();
  for (const { cell } of compared) {
    if (cell.canon === NOT_FOUND_CANON) continue;
    if (!isPlainObject(cell.value)) continue;
    for (const k of Object.keys(cell.value)) union.add(k);
  }
  const keys = [...union].sort((a, b) => a.localeCompare(b)).map((k) => {
    let first: string | null = null;
    let agree = true;
    for (const { cell } of compared) {
      let sub: unknown;
      if (cell.canon === NOT_FOUND_CANON) sub = undefined;
      else if (isPlainObject(cell.value)) sub = (cell.value as Record<string, unknown>)[k];
      else sub = undefined;
      const c = canonicalKey(sub);
      if (first === null) first = c;
      else if (c !== first) {
        agree = false;
        break;
      }
    }
    return { key: k, agree };
  });
  return { keys };
}

function finalizeRow(
  agentId: string,
  agentName: string,
  attr: string,
  attrType: string | undefined,
  cells: Record<string, CellState>,
  columns: CompareColumn[],
): CompareRow {
  const groups = new Map<string, string[]>();
  for (const col of columns) {
    const canon = cells[col.key]?.canon;
    if (
      !canon ||
      canon === PENDING_CANON ||
      canon === ERROR_CANON ||
      canon === NOT_SELECTED_CANON
    )
      continue;
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

  const comparedList: { key: string; cell: CellState }[] = [];
  for (const col of columns) {
    const cell = cells[col.key];
    if (!cell) continue;
    if (
      cell.canon === PENDING_CANON ||
      cell.canon === ERROR_CANON ||
      cell.canon === NOT_SELECTED_CANON
    )
      continue;
    comparedList.push({ key: col.key, cell });
  }
  const allEmpty =
    comparedList.length > 0 && comparedList.every(({ cell }) => cell.canon === NOT_FOUND_CANON);
  let score: number | null = null;
  if (comparedList.length >= 2) {
    const values = comparedList.map(({ cell }) =>
      cell.canon === NOT_FOUND_CANON ? undefined : cell.value,
    );
    score = rowAgreement(values, attrType);
  }

  let elementDiff: ElementDiff | undefined;
  if (!allEmpty && comparedList.length >= 1) {
    const nonMissing = comparedList.filter(({ cell }) => cell.canon !== NOT_FOUND_CANON);
    if (nonMissing.length > 0 && nonMissing.every(({ cell }) => Array.isArray(cell.value))) {
      elementDiff = buildItemsDiff(comparedList);
    } else if (
      nonMissing.length > 0 &&
      nonMissing.every(({ cell }) => isPlainObject(cell.value))
    ) {
      elementDiff = buildKeysDiff(comparedList);
    }
  }

  return {
    agentId,
    agentName,
    attr,
    cells,
    groups,
    state,
    ...(consensusKey !== undefined ? { consensusKey } : {}),
    score,
    allEmpty,
    ...(elementDiff !== undefined ? { elementDiff } : {}),
    ...(attrType !== undefined && attrType !== "" ? { attrType } : {}),
  };
}

/** Identifier fillable map: agent name -> attribute names (never throws). */
function fillableMapOf(out: unknown): Record<string, string[]> {
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
    if (!Array.isArray(attrs)) {
      if (!(name in map)) map[name] = [];
      continue;
    }
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

function selectedListOf(out: unknown): string[] | null {
  if (!out || typeof out !== "object" || Array.isArray(out)) return null;
  const v = (out as Record<string, unknown>).selected_agents;
  if (!Array.isArray(v)) return null;
  return v
    .filter((x): x is string => typeof x === "string")
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

function identifierCandidates(agent: CompareAgent, columns: CompareColumn[]): string[] {
  const seen = new Set<string>();
  const order: string[] = [];
  const add = (name: string) => {
    const n = (name ?? "").trim();
    if (n === "" || seen.has(n)) return;
    seen.add(n);
    order.push(n);
  };
  for (const col of columns) {
    const log = col.log;
    if (!isLiveColumn(col) || !log) continue;
    const out = agentOutput(log, agent.id, agent.name);
    if (hasAgentError(out) !== null) continue;
    if (!out || typeof out !== "object" || Array.isArray(out)) continue;
    const sel = selectedListOf(out);
    if (sel) for (const n of sel) add(n);
    const fmap = fillableMapOf(out);
    for (const n of Object.keys(fmap)) add(n);
  }
  return order;
}

/** Fixed 29 question keys for v2 identifier answers. */
const IDENTIFIER_QUESTION_KEYS = [
  "has_assets",
  "asset_bonds",
  "asset_cash",
  "asset_commodity",
  "asset_etfs",
  "asset_mutual_funds",
  "asset_crypto",
  "asset_fd",
  "asset_pension",
  "asset_stocks",
  "asset_personal_loans",
  "asset_real_estate",
  "asset_reits",
  "asset_other",
  "has_accounts",
  "credit_cards",
  "employment_changed",
  "employment_status_changed",
  "current_employer_mentioned",
  "job_transfer",
  "job_severance",
  "alumni",
  "education_institution",
  "expenses",
  "goals",
  "income",
  "insurance",
  "liabilities",
  "tax",
];

function hasNewIdentifierAnswers(out: unknown): boolean {
  if (!out || typeof out !== "object" || Array.isArray(out)) return false;
  const rec = out as Record<string, unknown>;
  if ("selected_agents" in rec || "fillable_attributes" in rec) return false;
  if ("_error" in rec) return false;
  return IDENTIFIER_QUESTION_KEYS.some((k) => typeof rec[k] === "boolean");
}

function buildIdentifierQuestionRows(
  agent: CompareAgent,
  columns: CompareColumn[],
): CompareRow[] {
  const rows: CompareRow[] = [];
  for (const qkey of IDENTIFIER_QUESTION_KEYS) {
    const cells: Record<string, CellState> = {};
    for (const col of columns) {
      const log = col.log;
      if (!log || !isLiveColumn(col)) {
        cells[col.key] = {
          value: undefined,
          canon: PENDING_CANON,
          error: col.error,
          feedback: null,
        };
        continue;
      }
      if (!hasAgentOutput(log, agent.id, agent.name)) {
        // Streaming provisional: expected but not yet returned => pending.
        if (col.status === "running" && snapshotHasAgent(log, agent.id)) {
          cells[col.key] = {
            value: undefined,
            canon: PENDING_CANON,
            error: col.error,
            feedback: null,
          };
          continue;
        }
        cells[col.key] = { value: undefined, canon: NOT_SELECTED_CANON, feedback: null };
        continue;
      }
      const out = agentOutput(log, agent.id, agent.name);
      const agentErr = hasAgentError(out);
      if (agentErr !== null) {
        cells[col.key] = { value: undefined, canon: ERROR_CANON, error: agentErr, feedback: null };
        continue;
      }
      if (!hasNewIdentifierAnswers(out)) {
        cells[col.key] = { value: undefined, canon: NOT_FOUND_CANON, feedback: null };
        continue;
      }
      const rec = out as Record<string, unknown>;
      const v = rec[qkey];
      const boolVal = v === true ? true : v === false ? false : undefined;
      const canon = boolVal === undefined ? NOT_FOUND_CANON : canonicalKey(boolVal);
      cells[col.key] = { value: boolVal, canon, feedback: null };
    }
    rows.push(finalizeRow(agent.id, agent.name, qkey, "boolean", cells, columns));
  }
  return rows;
}

function buildIdentifierRows(agent: CompareAgent, columns: CompareColumn[]): CompareRow[] {
  const rows: CompareRow[] = [];
  let anyOld = false;
  let anyNew = false;
  for (const col of columns) {
    const log = col.log;
    if (!log || !isLiveColumn(col)) continue;
    const out = agentOutput(log, agent.id, agent.name);
    if (hasAgentError(out) !== null) continue;
    if (selectedListOf(out) !== null) anyOld = true;
    else if (hasNewIdentifierAnswers(out)) anyNew = true;
    else {
      // Fallback: old logs without selection? Treat as old to keep rendering.
      if (out && typeof out === "object" && "fillable_attributes" in (out as Record<string, unknown>)) {
        anyOld = true;
      }
    }
  }
  // Old rendering (selected_agents + candidates).
  if (anyOld || (!anyOld && !anyNew)) {
    const selCells: Record<string, CellState> = {};
    for (const col of columns) {
      const log = col.log;
      if (!log || !isLiveColumn(col)) {
        selCells[col.key] = {
          value: undefined,
          canon: PENDING_CANON,
          error: col.error,
          feedback: null,
        };
        continue;
      }
      if (!hasAgentOutput(log, agent.id, agent.name)) {
        if (col.status === "running" && snapshotHasAgent(log, agent.id)) {
          selCells[col.key] = {
            value: undefined,
            canon: PENDING_CANON,
            error: col.error,
            feedback: null,
          };
          continue;
        }
        selCells[col.key] = { value: undefined, canon: NOT_SELECTED_CANON, feedback: null };
        continue;
      }
      const out = agentOutput(log, agent.id, agent.name);
      const agentErr = hasAgentError(out);
      if (agentErr !== null) {
        selCells[col.key] = { value: undefined, canon: ERROR_CANON, error: agentErr, feedback: null };
        continue;
      }
      if (selectedListOf(out) === null) {
        selCells[col.key] = { value: undefined, canon: NOT_FOUND_CANON, feedback: null };
        continue;
      }
      const part = extractCell(out, agent.kind, "selected_agents");
      const canon = canonicalKey(part.value);
      const lookupName = columnAgentName(log, agent.id, agent.name);
      selCells[col.key] = {
        value: part.value,
        confidence: part.confidence,
        confidenceType: part.confidenceType,
        evidence: part.evidence,
        canon,
        feedback: cellFeedback(log, lookupName, "selected_agents"),
      };
    }
    rows.push(finalizeRow(agent.id, agent.name, "selected_agents", undefined, selCells, columns));

    const candidates = identifierCandidates(agent, columns);
    for (const cand of candidates) {
      const cells: Record<string, CellState> = {};
      for (const col of columns) {
        const log = col.log;
        if (!log || !isLiveColumn(col)) {
          cells[col.key] = {
            value: undefined,
            canon: PENDING_CANON,
            error: col.error,
            feedback: null,
          };
          continue;
        }
        if (!hasAgentOutput(log, agent.id, agent.name)) {
          if (col.status === "running" && snapshotHasAgent(log, agent.id)) {
            cells[col.key] = {
              value: undefined,
              canon: PENDING_CANON,
              error: col.error,
              feedback: null,
            };
            continue;
          }
          cells[col.key] = { value: undefined, canon: NOT_SELECTED_CANON, feedback: null };
          continue;
        }
        const out = agentOutput(log, agent.id, agent.name);
        const agentErr = hasAgentError(out);
        if (agentErr !== null) {
          cells[col.key] = { value: undefined, canon: ERROR_CANON, error: agentErr, feedback: null };
          continue;
        }
        const lookupName = columnAgentName(log, agent.id, agent.name);
        const feedback = cellFeedback(log, lookupName, cand);
        const sel = selectedListOf(out);
        if (!sel || !sel.includes(cand)) {
          cells[col.key] = { value: undefined, canon: NOT_FOUND_CANON, feedback };
          continue;
        }
        const fmap = fillableMapOf(out);
        const list = fmap[cand] ?? [];
        cells[col.key] = { value: list, canon: canonicalKey(list), feedback };
      }
      rows.push(finalizeRow(agent.id, agent.name, cand, undefined, cells, columns));
    }
  }
  if (anyNew) {
    rows.push(...buildIdentifierQuestionRows(agent, columns));
  }
  return rows;
}

/**
 * One row per agent × attribute. Attribute order follows the agent snapshot,
 * then any extra output keys seen in the compared columns (first-seen order).
 * Done/partial columns plus streaming provisional logs (running with a log)
 * join the comparison; the rest get pending/error cells and are excluded
 * from `groups`. Missing output in a running provisional column means still
 * running (pending) when the snapshot lists the agent, else not selected.
 * Identifier agents instead produce one row per candidate agent name plus a
 * `selected_agents` row (no raw `fillable_attributes` row).
 */
export function buildRows(agents: CompareAgent[], columns: CompareColumn[]): CompareRow[] {
  const rows: CompareRow[] = [];
  for (const agent of agents) {
    if (agent.kind === "identifier") {
      rows.push(...buildIdentifierRows(agent, columns));
      continue;
    }
    const attrNames = agent.attributes.map((a) => a.name);
    const seen = new Set(attrNames);
    const extras: string[] = [];
    for (const col of columns) {
      if (!col.log || !isLiveColumn(col)) continue;
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
      const attrMeta = agent.attributes.find((a) => a.name === attr);
      const attrType = attrMeta?.type;
      const cells: Record<string, CellState> = {};
      for (const col of columns) {
        const log = col.log;
        if (!log || !isLiveColumn(col)) {
          cells[col.key] = {
            value: undefined,
            canon: PENDING_CANON,
            error: col.error,
            feedback: null,
          };
          continue;
        }
        if (!hasAgentOutput(log, agent.id, agent.name)) {
          if (col.status === "running" && snapshotHasAgent(log, agent.id)) {
            cells[col.key] = {
              value: undefined,
              canon: PENDING_CANON,
              error: col.error,
              feedback: null,
            };
            continue;
          }
          cells[col.key] = { value: undefined, canon: NOT_SELECTED_CANON, feedback: null };
          continue;
        }
        const out = agentOutput(log, agent.id, agent.name);
        const agentErr = hasAgentError(out);
        if (agentErr !== null) {
          cells[col.key] = { value: undefined, canon: ERROR_CANON, error: agentErr, feedback: null };
          continue;
        }
        const rawEntry =
          out && typeof out === "object" && !Array.isArray(out)
            ? (out as Record<string, unknown>)[attr]
            : undefined;
        const unwrapped = isUnwrappedAttr(attrMeta, rawEntry);
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
          ...(unwrapped ? { unwrapped: true as const } : {}),
        };
      }
      rows.push(finalizeRow(agent.id, agent.name, attr, attrType, cells, columns));
    }
  }
  return rows;
}

/** Rows comparable for agreement: not allEmpty and at least 2 compared. */
function isComparableRow(r: CompareRow): boolean {
  return !r.allEmpty && r.score !== null;
}

function comparedValues(row: CompareRow): { keys: string[]; values: unknown[] } {
  const keys: string[] = [];
  const values: unknown[] = [];
  for (const [k, cell] of Object.entries(row.cells)) {
    if (
      !cell ||
      cell.canon === PENDING_CANON ||
      cell.canon === ERROR_CANON ||
      cell.canon === NOT_SELECTED_CANON
    )
      continue;
    keys.push(k);
    values.push(cell.canon === NOT_FOUND_CANON ? undefined : cell.value);
  }
  return { keys, values };
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
  let sum = 0;
  let denom = 0;
  for (const r of rows) {
    if (!isComparableRow(r)) continue;
    const { keys, values } = comparedValues(r);
    const idx = keys.indexOf(colKey);
    if (idx < 0) continue;
    denom += 1;
    sum += peerAgreement(values, idx, r.attrType);
  }
  return {
    agreePct: denom === 0 ? 0 : (sum / denom) * 100,
    up,
    down,
    rated: up + down,
    total: rows.length,
  };
}

/** Other columns holding the same canonical value in this row. */
export function linkedTargets(_rows: CompareRow[], row: CompareRow, colKey: string): string[] {
  const cell = row.cells[colKey];
  if (
    !cell ||
    cell.canon === PENDING_CANON ||
    cell.canon === ERROR_CANON ||
    cell.canon === NOT_SELECTED_CANON
  )
    return [];
  const group = row.groups.get(cell.canon);
  if (!group) return [];
  return group.filter((k) => k !== colKey);
}

/**
 * Group agreement % (0-100): mean row score over comparable rows (excluding
 * allEmpty rows and rows with <2 compared). 0 when there are none.
 */
export function groupAgreementPct(rows: CompareRow[]): number {
  let sum = 0;
  let n = 0;
  for (const r of rows) {
    if (!isComparableRow(r)) continue;
    sum += r.score as number;
    n += 1;
  }
  return n === 0 ? 0 : (sum / n) * 100;
}

/** Number of allEmpty rows (empty in every compared column). */
export function emptyRowCount(rows: CompareRow[]): number {
  let n = 0;
  for (const r of rows) {
    if (r.allEmpty) n += 1;
  }
  return n;
}

/**
 * Short model name: the part after the FIRST "/" (e.g. "anthropic/claude-sonnet-4"
 * -> "claude-sonnet-4", "typesafe/jev-router" -> "jev-router"). No slash -> whole id.
 */
export function modelShortName(modelId: string): string {
  const id = typeof modelId === "string" ? modelId : "";
  const i = id.indexOf("/");
  return i >= 0 ? id.slice(i + 1) : id;
}

function normStr(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/**
 * Alphabetical column order: by short model name (case-insensitive), then
 * effort, then provider. Stable and independent of slot/completion order.
 * Works for CompareColumn and LogRow shapes (model/effort/provider).
 */
export function compareColumnsByModel(
  a: { model?: unknown; effort?: unknown; reasoning_effort?: unknown; provider?: unknown; key?: unknown; id?: unknown },
  b: { model?: unknown; effort?: unknown; reasoning_effort?: unknown; provider?: unknown; key?: unknown; id?: unknown },
): number {
  const aModel = normStr(a.model);
  const bModel = normStr(b.model);
  const aShort = modelShortName(aModel).toLowerCase();
  const bShort = modelShortName(bModel).toLowerCase();
  if (aShort !== bShort) return aShort < bShort ? -1 : 1;
  const aEff = normStr((a as { effort?: unknown }).effort ?? (a as { reasoning_effort?: unknown }).reasoning_effort).toLowerCase();
  const bEff = normStr((b as { effort?: unknown }).effort ?? (b as { reasoning_effort?: unknown }).reasoning_effort).toLowerCase();
  if (aEff !== bEff) return aEff < bEff ? -1 : 1;
  const aProv = normStr(a.provider).toLowerCase();
  const bProv = normStr(b.provider).toLowerCase();
  if (aProv !== bProv) return aProv < bProv ? -1 : 1;
  // Final tie-breakers for total stability: full id, then key/id.
  const aFull = aModel.toLowerCase();
  const bFull = bModel.toLowerCase();
  if (aFull !== bFull) return aFull < bFull ? -1 : 1;
  const aKey = String((a as { key?: unknown }).key ?? (a as { id?: unknown }).id ?? "");
  const bKey = String((b as { key?: unknown }).key ?? (b as { id?: unknown }).id ?? "");
  if (aKey !== bKey) return aKey < bKey ? -1 : 1;
  return 0;
}

/** Sorted copy of compare columns (alphabetical by model, see above). */
export function sortCompareColumns<T extends { model?: unknown; effort?: unknown; provider?: unknown; key?: unknown }>(
  columns: T[],
): T[] {
  return [...(columns ?? [])].sort(compareColumnsByModel);
}

/** Sorted copy of log rows (alphabetical by model, for selectors/lists). */
export function sortLogsByModel<T extends { model?: unknown; reasoning_effort?: unknown; provider?: unknown; id?: unknown }>(
  rows: T[],
): T[] {
  return [...(rows ?? [])].sort(compareColumnsByModel);
}
