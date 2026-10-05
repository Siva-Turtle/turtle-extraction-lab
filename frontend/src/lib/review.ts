// Review page helpers (pure functions, no React): Jev-vs-Opus identifier
// eval pairs. One ReviewGroup per run_group_id that has both a
// `typesafe/jev-*` log and an `anthropic/*` log; one ReviewRow per group x
// per identifier question key.

import { IDENTIFIER_QUESTION_KEYS, identifierAnswersOf } from "./format";
import { groupKeyOf } from "./logGroups";
import type { LogRow } from "./logTypes";

export type ReviewChunk = { n: number; text: string };

export type ReviewFeedback = { rating: string; remarks: string; auto?: boolean };

export type ReviewSide = {
  /** Identifier answer (null = missing on old/malformed rows). */
  value: boolean | null;
  /** Noul probability (Jev floats; Opus nulls). */
  prob: number | null;
  /** Cited chunk number (null = none). */
  evidence: number | null;
  runId: string;
  /** Snapshot agent name used for feedback writes/reads. */
  agentName: string;
  feedback: ReviewFeedback | null;
};

export type ReviewRow = {
  key: string;
  groupId: string;
  sample: string;
  questionKey: string;
  jev: ReviewSide;
  opus: ReviewSide;
  agree: boolean;
  firefliesUrl: string;
  chunks: ReviewChunk[];
};

export type ReviewGroup = {
  id: string;
  title: string;
  sample: string;
  createdAt: string;
  jev: LogRow;
  opus: LogRow;
  jevModel: string;
  opusModel: string;
  /** Summed cost_usd over both logs, or null when neither reports one. */
  cost: number | null;
  chunks: ReviewChunk[];
};

/** Case-insensitive `typesafe/jev-*` match (mirrors the backend decisions prefix). */
export function isJevModel(model: unknown): boolean {
  try {
    const m = typeof model === "string" ? model.trim().toLowerCase() : "";
    return m.startsWith("typesafe/jev-");
  } catch {
    return false;
  }
}

/** Case-insensitive `anthropic/*` match. */
export function isOpusModel(model: unknown): boolean {
  try {
    const m = typeof model === "string" ? model.trim().toLowerCase() : "";
    return m.startsWith("anthropic/");
  } catch {
    return false;
  }
}

/** Sortable timestamp; invalid timestamps sink last. */
function timeOf(ts: string): number {
  const t = new Date(ts).getTime();
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
}

/** Latest log in a list by created_at (later input order wins ties). */
function latestOf(logs: LogRow[]): LogRow {
  let best = logs[0] as LogRow;
  for (const l of logs) {
    if (timeOf(l.created_at) >= timeOf(best.created_at)) best = l;
  }
  return best;
}

/** Identifier agent {id, name} for one log (snapshot kind match, then the
 * consistency snapshot's identifier_agent_id). Null when unresolvable. */
function identifierAgentOf(log: LogRow): { id: string; name: string } | null {
  try {
    const snap = (log.agent_snapshot ?? {}) as Record<string, { name?: unknown; kind?: unknown }>;
    for (const [id, raw] of Object.entries(snap)) {
      const kind = typeof raw?.kind === "string" ? raw.kind.trim().toLowerCase() : "";
      if (kind === "identifier") {
        const name = typeof raw?.name === "string" && raw.name.trim() !== "" ? raw.name : id;
        return { id, name };
      }
    }
    const iid = (log.consistency ?? {}) as { identifier_agent_id?: unknown };
    if (typeof iid.identifier_agent_id === "string" && iid.identifier_agent_id.trim() !== "") {
      const id = iid.identifier_agent_id;
      const raw = snap[id];
      const name =
        raw && typeof raw.name === "string" && raw.name.trim() !== "" ? raw.name : "agent_identifier";
      return { id, name };
    }
  } catch {
    // fall through to null
  }
  return null;
}

/** Raw identifier output object (keyed by agent id, then snapshot name). */
function identifierOutputOf(log: LogRow, agentId: string, agentName: string): unknown {
  try {
    const outs = (log.outputs ?? {}) as Record<string, unknown>;
    if (outs[agentId] !== undefined && outs[agentId] !== null) return outs[agentId];
    if (agentName !== agentId && outs[agentName] !== undefined && outs[agentName] !== null) {
      return outs[agentName];
    }
  } catch {
    // ignore
  }
  return undefined;
}

/** Identifier boolean answers, or null when this log has no v2 identifier
 * output. Missing keys stay null (unlike identifierAnswersOf's false-fill). */
function answersOf(log: LogRow, agentId: string, agentName: string): Record<string, boolean | null> | null {
  const out = identifierOutputOf(log, agentId, agentName);
  if (identifierAnswersOf(out) === null) return null;
  const rec = out as Record<string, unknown>;
  const ans: Record<string, boolean | null> = {};
  for (const k of IDENTIFIER_QUESTION_KEYS) {
    const v = rec[k];
    ans[k] = v === true ? true : v === false ? false : null;
  }
  return ans;
}

/** One numeric side-map entry ({agentId|agentName: {questionKey: n}}). */
function numEntryOf(
  top: unknown,
  agentId: string,
  agentName: string,
  key: string,
): number | null {
  try {
    if (!top || typeof top !== "object" || Array.isArray(top)) return null;
    const rec = top as Record<string, unknown>;
    for (const aid of [agentId, agentName]) {
      const byAgent = rec[aid];
      if (!byAgent || typeof byAgent !== "object" || Array.isArray(byAgent)) continue;
      const v = (byAgent as Record<string, unknown>)[key];
      if (typeof v === "number" && Number.isFinite(v)) return v;
    }
  } catch {
    // ignore
  }
  return null;
}

/** Saved feedback for one agent/attribute (mirrors compare.ts cellFeedback). */
function savedFeedbackOf(
  log: LogRow,
  agentName: string,
  attr: string,
): ReviewFeedback | null {
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

/** Chunk list from one stored request body: decisions `state.chunks`
 * ([{n, text}]) first, then the chat user message (`messages[1].content`
 * with "[n] ..." lines). [] when neither parses. Never throws. */
function chunksFromRequestBody(body: unknown): ReviewChunk[] {
  try {
    if (!body || typeof body !== "object" || Array.isArray(body)) return [];
    const rec = body as Record<string, unknown>;
    const state = rec.state;
    if (state && typeof state === "object" && !Array.isArray(state)) {
      const raw = (state as Record<string, unknown>).chunks;
      if (Array.isArray(raw) && raw.length > 0) {
        const out: ReviewChunk[] = [];
        for (const c of raw) {
          if (!c || typeof c !== "object" || Array.isArray(c)) continue;
          const n = (c as Record<string, unknown>).n;
          const t = (c as Record<string, unknown>).text;
          if (typeof n === "number" && Number.isInteger(n) && n >= 1 && typeof t === "string") {
            out.push({ n, text: t });
          }
        }
        if (out.length > 0) return out;
      }
    }
    const messages = rec.messages;
    if (Array.isArray(messages)) {
      const candidates: unknown[] = [];
      if (messages.length > 1) candidates.push(messages[1]);
      for (const m of messages) candidates.push(m);
      for (const m of candidates) {
        if (!m || typeof m !== "object" || Array.isArray(m)) continue;
        const msg = m as Record<string, unknown>;
        if (msg.role !== undefined && msg.role !== "user") continue;
        if (typeof msg.content !== "string" || msg.content.trim() === "") continue;
        const parsed = parseNumberedChunks(msg.content);
        if (parsed.length > 0) return parsed;
      }
    }
  } catch {
    // ignore
  }
  return [];
}

/** Parse `"[1] <text>\n\n[2] <text>..."` into chunks (marker-sliced, so
 * chunk text may itself contain blank lines). Never throws. */
export function parseNumberedChunks(content: string): ReviewChunk[] {
  const out: ReviewChunk[] = [];
  try {
    if (typeof content !== "string" || content.trim() === "") return out;
    const re = /\[(\d+)\]/g;
    const marks: { n: number; start: number; end: number }[] = [];
    let m: RegExpExecArray | null;
    while ((m = re.exec(content)) !== null) {
      const n = parseInt(m[1] as string, 10);
      if (!Number.isInteger(n) || n < 1) continue;
      marks.push({ n, start: m.index, end: m.index + m[0].length });
    }
    for (let i = 0; i < marks.length; i += 1) {
      const cur = marks[i] as { n: number; start: number; end: number };
      const next = marks[i + 1];
      const text = content.slice(cur.end, next ? next.start : content.length).trim();
      if (text === "") continue;
      if (out.some((c) => c.n === cur.n)) continue;
      out.push({ n: cur.n, text });
    }
  } catch {
    // ignore
  }
  return out;
}

/** Chunk list from one log's stored requests (identifier agent's body;
 * scans every request body as a last resort). Never throws. */
function chunksFromLog(log: LogRow, agentId: string, agentName: string): ReviewChunk[] {
  try {
    const reqs = (log.requests ?? {}) as Record<string, unknown>;
    const keys = [agentId, agentName].filter((k, i, a) => k !== "" && a.indexOf(k) === i);
    for (const k of keys) {
      if (!(k in reqs)) continue;
      const chunks = chunksFromRequestBody(reqs[k]);
      if (chunks.length > 0) return chunks;
    }
    for (const body of Object.values(reqs)) {
      const chunks = chunksFromRequestBody(body);
      if (chunks.length > 0) return chunks;
    }
  } catch {
    // ignore
  }
  return [];
}

/** Chunk text for one chunk number ("" when missing). */
export function chunkText(chunks: ReviewChunk[], n: number | null): string {
  try {
    if (n === null || n === undefined) return "";
    const found = (chunks ?? []).find((c) => c.n === n);
    return found && typeof found.text === "string" ? found.text : "";
  } catch {
    return "";
  }
}

/** Sample label for a log row: meeting title, then client, then "Untitled run". */
function sampleOf(log: LogRow): string {
  try {
    const t = typeof log.meeting_title === "string" ? log.meeting_title.trim() : "";
    if (t !== "") return t;
    const c = typeof log.client === "string" ? log.client.trim() : "";
    if (c !== "") return c;
  } catch {
    // ignore
  }
  return "Untitled run";
}

function finiteCost(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function firefliesOf(log: LogRow): string {
  try {
    const u = typeof log.fireflies_url === "string" ? log.fireflies_url.trim() : "";
    return u;
  } catch {
    return "";
  }
}

function sideOf(log: LogRow, key: string): ReviewSide {
  const agent = identifierAgentOf(log);
  const agentId = agent ? agent.id : "agent_identifier";
  const agentName = agent ? agent.name : "agent_identifier";
  const answers = answersOf(log, agentId, agentName);
  const evRaw = numEntryOf(log.evidence, agentId, agentName, key);
  const prRaw = numEntryOf(log.probabilities, agentId, agentName, key);
  return {
    value: answers ? (answers[key] ?? null) : null,
    prob: prRaw,
    evidence:
      evRaw !== null && Number.isInteger(evRaw) && (evRaw as number) >= 1 ? evRaw : null,
    runId: typeof log.run_id === "string" ? log.run_id : "",
    agentName,
    feedback: savedFeedbackOf(log, agentName, key),
  };
}

/**
 * Group logs by run_group_id (or id) and keep the groups that pair one
 * `typesafe/jev-*` log with one `anthropic/*` log (latest of each by
 * created_at). Chunks come from the Jev (decisions) log's `state.chunks`,
 * falling back to parsing the chat user message (either log). Groups sort
 * newest-first. Never throws.
 */
export function pairGroups(logs: LogRow[]): ReviewGroup[] {
  const byKey = new Map<string, LogRow[]>();
  try {
    for (const log of logs ?? []) {
      if (!log || typeof log !== "object") continue;
      const k = groupKeyOf(log);
      const list = byKey.get(k);
      if (list) list.push(log);
      else byKey.set(k, [log]);
    }
  } catch {
    return [];
  }
  const groups: ReviewGroup[] = [];
  for (const [key, members] of byKey) {
    try {
      const jevs = members.filter((l) => isJevModel(l.model));
      const opuses = members.filter((l) => isOpusModel(l.model));
      if (jevs.length === 0 || opuses.length === 0) continue;
      const jev = latestOf(jevs);
      const opus = latestOf(opuses);
      const jevAgent = identifierAgentOf(jev);
      const opusAgent = identifierAgentOf(opus);
      let chunks = chunksFromLog(
        jev,
        jevAgent ? jevAgent.id : "agent_identifier",
        jevAgent ? jevAgent.name : "agent_identifier",
      );
      if (chunks.length === 0) {
        chunks = chunksFromLog(
          opus,
          opusAgent ? opusAgent.id : "agent_identifier",
          opusAgent ? opusAgent.name : "agent_identifier",
        );
      }
      const costs = [finiteCost(jev.usage?.cost_usd), finiteCost(opus.usage?.cost_usd)];
      const hasCost = costs.some((c) => c !== null);
      const title = sampleOf(jev) !== "Untitled run" ? sampleOf(jev) : sampleOf(opus);
      const createdAt =
        timeOf(jev.created_at) >= timeOf(opus.created_at) ? jev.created_at : opus.created_at;
      groups.push({
        id: key,
        title,
        sample: title,
        createdAt,
        jev,
        opus,
        jevModel: typeof jev.model === "string" ? jev.model : "",
        opusModel: typeof opus.model === "string" ? opus.model : "",
        cost: hasCost ? (costs[0] ?? 0) + (costs[1] ?? 0) : null,
        chunks,
      });
    } catch {
      // skip malformed groups, keep the rest
    }
  }
  groups.sort((a, b) => timeOf(b.createdAt) - timeOf(a.createdAt));
  return groups;
}

/**
 * One row per group x per identifier question key. `agree` is true when
 * both sides hold the same value (including both missing — that is not a
 * discrepancy). Never throws.
 */
export function buildRows(groups: ReviewGroup[]): ReviewRow[] {
  const rows: ReviewRow[] = [];
  try {
    for (const g of groups ?? []) {
      if (!g) continue;
      const firefliesUrl = firefliesOf(g.jev) !== "" ? firefliesOf(g.jev) : firefliesOf(g.opus);
      const sample = g.sample || g.title || "Untitled run";
      for (const key of IDENTIFIER_QUESTION_KEYS) {
        try {
          const jev = sideOf(g.jev, key);
          const opus = sideOf(g.opus, key);
          rows.push({
            key: `${g.id}|${key}`,
            groupId: g.id,
            sample,
            questionKey: key,
            jev,
            opus,
            agree: jev.value === opus.value,
            firefliesUrl,
            chunks: g.chunks ?? [],
          });
        } catch {
          // skip malformed rows, keep the rest
        }
      }
    }
  } catch {
    // ignore
  }
  return rows;
}

// ---------------------------------------------------------------------------
// Batches (auto-clustered eval runs) + row stars (browser-local bookmarks).
// All browser persistence is frontend-local localStorage; never throws.
// ---------------------------------------------------------------------------

/** Gap that splits one batch from the next (3h). */
export const BATCH_GAP_MS = 3 * 60 * 60 * 1000;

export type ReviewBatch = {
  /** Stable id = first (oldest) group id in the batch. */
  id: string;
  /** Default name "Run N" (N = 1-based oldest-first); overridable in storage. */
  name: string;
  groupIds: string[];
  /** createdAt of the first (oldest) group. */
  startedAt: string;
};

export const BATCH_NAMES_KEY = "review:batchNames";
export const BATCH_SEL_KEY = "review:batchSel";
export const STARS_KEY = "review:stars";

export type BatchSelection = {
  /** Selected batch id; null = All batches. */
  batchId: string | null;
  selected: string[];
};

function storageGet(key: string): string | null {
  try {
    if (typeof window === "undefined" || !window.localStorage) return null;
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(key: string, value: string): void {
  try {
    if (typeof window === "undefined" || !window.localStorage) return;
    window.localStorage.setItem(key, value);
  } catch {
    // ignore (private mode etc.)
  }
}

/**
 * Auto-cluster groups into batches: sort by newest-log time ascending,
 * split into a new batch wherever the gap to the previous group exceeds
 * BATCH_GAP_MS. Oldest batch first ("Run 1" …). Never throws.
 */
export function clusterBatches(groups: ReviewGroup[]): ReviewBatch[] {
  try {
    const sorted = [...(groups ?? [])]
      .filter((g) => g && typeof g.id === "string")
      .sort((a, b) => timeOf(a.createdAt) - timeOf(b.createdAt));
    const batches: ReviewBatch[] = [];
    let cur: ReviewGroup[] = [];
    let prevT = Number.NEGATIVE_INFINITY;
    let prevValid = false;
    for (const g of sorted) {
      const t = timeOf(g.createdAt);
      const valid = Number.isFinite(t) && t !== Number.NEGATIVE_INFINITY;
      if (cur.length > 0 && valid && prevValid && t - prevT > BATCH_GAP_MS) {
        const n = batches.length + 1;
        batches.push({
          id: cur[0].id,
          name: `Run ${n}`,
          groupIds: cur.map((x) => x.id),
          startedAt: cur[0].createdAt,
        });
        cur = [];
      }
      cur.push(g);
      if (valid) {
        prevT = t;
        prevValid = true;
      }
    }
    if (cur.length > 0) {
      const n = batches.length + 1;
      batches.push({
        id: cur[0].id,
        name: `Run ${n}`,
        groupIds: cur.map((x) => x.id),
        startedAt: cur[0].createdAt,
      });
    }
    return batches;
  } catch {
    return [];
  }
}

/** Custom batch names keyed by batch id. Never throws. */
export function loadBatchNames(): Record<string, string> {
  try {
    const raw = storageGet(BATCH_NAMES_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === "string" && v.trim() !== "") out[k] = v;
    }
    return out;
  } catch {
    return {};
  }
}

export function saveBatchNames(names: Record<string, string>): void {
  try {
    const clean: Record<string, string> = {};
    for (const [k, v] of Object.entries(names ?? {})) {
      if (typeof v === "string" && v.trim() !== "") clean[k] = v;
    }
    storageSet(BATCH_NAMES_KEY, JSON.stringify(clean));
  } catch {
    // ignore
  }
}

/** Display name for a batch (custom override wins). Never throws. */
export function batchDisplayName(
  batch: ReviewBatch,
  overrides?: Record<string, string>,
): string {
  try {
    const custom = overrides?.[batch.id];
    if (typeof custom === "string" && custom.trim() !== "") return custom;
    return batch.name;
  } catch {
    return batch.name;
  }
}

/** Persisted batch + group selection; null when nothing stored yet. */
export function loadBatchSel(): BatchSelection | null {
  try {
    const raw = storageGet(BATCH_SEL_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<BatchSelection> | null;
    if (!parsed || typeof parsed !== "object") return null;
    const batchId =
      parsed.batchId === null || typeof parsed.batchId === "string"
        ? parsed.batchId
        : null;
    const selected = Array.isArray(parsed.selected)
      ? parsed.selected.filter((s): s is string => typeof s === "string")
      : null;
    if (selected === null) return null;
    return { batchId, selected };
  } catch {
    return null;
  }
}

export function saveBatchSel(sel: BatchSelection): void {
  try {
    storageSet(
      BATCH_SEL_KEY,
      JSON.stringify({ batchId: sel.batchId ?? null, selected: sel.selected ?? [] }),
    );
  } catch {
    // ignore
  }
}

/** Starred row keys (`${groupId}|${questionKey}`). Never throws. */
export function loadStars(): string[] {
  try {
    const raw = storageGet(STARS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((s): s is string => typeof s === "string");
  } catch {
    return [];
  }
}

export function saveStars(stars: string[]): void {
  try {
    const clean = (stars ?? []).filter((s) => typeof s === "string");
    storageSet(STARS_KEY, JSON.stringify(clean));
  } catch {
    // ignore
  }
}

export function isStarred(stars: string[], key: string): boolean {
  try {
    return (stars ?? []).includes(key);
  } catch {
    return false;
  }
}

export function toggleStar(stars: string[], key: string): string[] {
  try {
    const cur = (stars ?? []).filter((s) => typeof s === "string");
    if (cur.includes(key)) return cur.filter((s) => s !== key);
    return [...cur, key];
  } catch {
    return stars ?? [];
  }
}
