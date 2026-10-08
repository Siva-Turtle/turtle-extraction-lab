// Review page helpers (pure functions, no React): Jev-vs-Opus identifier
// eval pairs. One ReviewGroup per meeting_id that has a `typesafe/jev-*`
// log and/or an `anthropic/*` log (either side may be absent); logs with no
// meeting fall back to one group per run_group_id-or-id. One ReviewRow per
// group x per identifier question key.

import { IDENTIFIER_QUESTION_KEYS, identifierAnswersOf } from "./format";
import { api } from "./api";
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
  /** Failed-run message from `outputs[agentId]._error` (null when the run succeeded). */
  error: string | null;
};

export type ReviewRow = {
  key: string;
  groupId: string;
  sample: string;
  questionKey: string;
  /** Exact question text sent to the model ("" when unresolvable). */
  question: string;
  jev: ReviewSide;
  opus: ReviewSide;
  agree: boolean;
  /** False when either side's answers are missing (single-sided pair or a
   * side with no v2 identifier output). NOT the same as errored. */
  comparable: boolean;
  firefliesUrl: string;
  chunks: ReviewChunk[];
  /** Failed-run message for the Jev side (`outputs[agentId]._error`, null when ok). */
  jevError: string | null;
  /** Failed-run message for the Opus side (`outputs[agentId]._error`, null when ok). */
  opusError: string | null;
};

export type ReviewGroup = {
  id: string;
  title: string;
  sample: string;
  createdAt: string;
  /** Newest `typesafe/jev-*` log for the meeting (null on Opus-only pairs). */
  jev: LogRow | null;
  /** Newest `anthropic/*` log for the meeting (null on Jev-only pairs). */
  opus: LogRow | null;
  jevModel: string;
  opusModel: string;
  /** Summed cost_usd over both logs, or null when neither reports one. */
  cost: number | null;
  chunks: ReviewChunk[];
  /** Failed-run message for the Jev log (`outputs[agentId]._error`, null when ok). */
  jevError: string | null;
  /** Failed-run message for the Opus log (`outputs[agentId]._error`, null when ok). */
  opusError: string | null;
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

/** Non-blank `filters.meeting_id` snapshot ("" when absent, e.g. old rows). */
export function meetingIdOf(log: LogRow | null): string {
  try {
    if (!log || typeof log !== "object") return "";
    const f = (log as LogRow).filters;
    if (!f || typeof f !== "object") return "";
    const m = (f as { meeting_id?: unknown }).meeting_id;
    return typeof m === "string" ? m.trim() : "";
  } catch {
    return "";
  }
}

/**
 * Pair key for one log: `meeting:<meeting_id>` when the log carries one,
 * else the legacy run_group_id-or-id key (groupKeyOf). Never throws.
 */
export function pairKeyOf(log: LogRow): string {
  try {
    const m = meetingIdOf(log);
    if (m !== "") return `meeting:${m}`;
    return groupKeyOf(log);
  } catch {
    try {
      return groupKeyOf(log);
    } catch {
      return "";
    }
  }
}

/** Identifier agent {id, name} for one log (snapshot kind match, then the
 * consistency snapshot's identifier_agent_id). Null when unresolvable or
 * the log is null (single-sided pair). */
function identifierAgentOf(log: LogRow | null): { id: string; name: string } | null {
  try {
    if (!log || typeof log !== "object") return null;
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

/**
 * Failed-run message for one log's identifier output: `outputs[agentId]?._error`
 * (string) when the model's run failed, else null. Checks the snapshot name
 * key as a fallback (mirrors identifierOutputOf). Never throws.
 */
export function errorOf(log: LogRow): string | null {
  try {
    if (!log || typeof log !== "object") return null;
    const agent = identifierAgentOf(log);
    const agentId = agent ? agent.id : "agent_identifier";
    const agentName = agent ? agent.name : "agent_identifier";
    const outs = (log.outputs ?? {}) as Record<string, unknown>;
    for (const aid of [agentId, agentName]) {
      if (typeof aid !== "string" || aid === "") continue;
      const out = outs[aid];
      if (!out || typeof out !== "object" || Array.isArray(out)) continue;
      const err = (out as Record<string, unknown>)._error;
      if (typeof err === "string" && err.trim() !== "") return err;
    }
  } catch {
    // ignore
  }
  return null;
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
 * scans every request body as a last resort). [] for a null log
 * (single-sided pair). Never throws. */
function chunksFromLog(log: LogRow | null, agentId: string, agentName: string): ReviewChunk[] {
  try {
    if (!log || typeof log !== "object") return [];
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
function sampleOf(log: LogRow | null): string {
  try {
    if (!log || typeof log !== "object") return "Untitled run";
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

function firefliesOf(log: LogRow | null): string {
  try {
    if (!log || typeof log !== "object") return "";
    const u = typeof log.fireflies_url === "string" ? log.fireflies_url.trim() : "";
    return u;
  } catch {
    return "";
  }
}

function sideOf(log: LogRow | null, key: string): ReviewSide {
  if (!log || typeof log !== "object") {
    return {
      value: null,
      prob: null,
      evidence: null,
      runId: "",
      agentName: "agent_identifier",
      feedback: null,
      error: null,
    };
  }
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
    error: errorOf(log),
  };
}

/**
 * Exact question text sent to the model, read from the log's own stored
 * request (what the model actually saw). Decisions body
 * (`requests[agentId].questions?.[qkey]?.instructions`) wins verbatim;
 * else scans `requests[agentId].messages` system content for the line
 * matching `^Q\d+ \(qkey\):` and returns the full line trimmed. ""
 * when unresolvable. Never throws.
 */
export function questionText(log: LogRow | null, agentId: string, qkey: string): string {
  try {
    if (!log || typeof log !== "object") return "";
    if (typeof agentId !== "string" || agentId === "") return "";
    if (typeof qkey !== "string" || qkey === "") return "";
    const reqs = (log.requests ?? {}) as Record<string, unknown>;
    if (!reqs || typeof reqs !== "object" || Array.isArray(reqs)) return "";
    const body = reqs[agentId];
    if (!body || typeof body !== "object" || Array.isArray(body)) return "";
    const rec = body as Record<string, unknown>;
    try {
      const qs = rec.questions;
      if (qs && typeof qs === "object" && !Array.isArray(qs)) {
        const entry = (qs as Record<string, unknown>)[qkey];
        if (entry && typeof entry === "object" && !Array.isArray(entry)) {
          const instr = (entry as Record<string, unknown>).instructions;
          if (typeof instr === "string" && instr !== "") return instr;
        }
      }
    } catch {
      // fall through to chat body
    }
    try {
      const messages = rec.messages;
      if (!Array.isArray(messages)) return "";
      const escaped = qkey.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const re = new RegExp(`^Q\\d+ \\(${escaped}\\):`, "m");
      for (const m of messages) {
        if (!m || typeof m !== "object" || Array.isArray(m)) continue;
        const msg = m as Record<string, unknown>;
        if (msg.role !== "system") continue;
        if (typeof msg.content !== "string" || msg.content === "") continue;
        const lines = msg.content.split("\n");
        for (const line of lines) {
          try {
            if (re.test(line)) {
              const t = line.trim();
              if (t !== "") return t;
            }
          } catch {
            // ignore bad lines, keep scanning
          }
        }
      }
    } catch {
      // ignore
    }
    return "";
  } catch {
    return "";
  }
}

/**
 * Group logs by meeting (`meeting:<meeting_id>`, else the legacy
 * run_group_id-or-id key) and keep the newest `typesafe/jev-*` log and
 * the newest `anthropic/*` log per meeting by created_at. Either side may
 * be absent (single-sided pair); only groups with neither side are
 * skipped. Chunks come from the Jev (decisions) log's `state.chunks`,
 * falling back to parsing the chat user message (either log). Groups sort
 * newest-first. Never throws.
 */
export function pairGroups(logs: LogRow[]): ReviewGroup[] {
  const byKey = new Map<string, LogRow[]>();
  try {
    for (const log of logs ?? []) {
      if (!log || typeof log !== "object") continue;
      const k = pairKeyOf(log);
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
      if (jevs.length === 0 && opuses.length === 0) continue;
      const jev = jevs.length > 0 ? latestOf(jevs) : null;
      const opus = opuses.length > 0 ? latestOf(opuses) : null;
      const jevAgent = identifierAgentOf(jev);
      const opusAgent = identifierAgentOf(opus);
      let chunks: ReviewChunk[] = [];
      if (jev) {
        chunks = chunksFromLog(
          jev,
          jevAgent ? jevAgent.id : "agent_identifier",
          jevAgent ? jevAgent.name : "agent_identifier",
        );
      }
      if (chunks.length === 0 && opus) {
        chunks = chunksFromLog(
          opus,
          opusAgent ? opusAgent.id : "agent_identifier",
          opusAgent ? opusAgent.name : "agent_identifier",
        );
      }
      const costs = [
        jev ? finiteCost(jev.usage?.cost_usd) : null,
        opus ? finiteCost(opus.usage?.cost_usd) : null,
      ];
      const hasCost = costs.some((c) => c !== null);
      const jevTitle = sampleOf(jev);
      const title = jevTitle !== "Untitled run" ? jevTitle : sampleOf(opus);
      let createdAt = "";
      if (jev && opus) {
        createdAt =
          timeOf(jev.created_at) >= timeOf(opus.created_at) ? jev.created_at : opus.created_at;
      } else if (jev) {
        createdAt = jev.created_at;
      } else if (opus) {
        createdAt = opus.created_at;
      }
      const jevError = jev ? errorOf(jev) : null;
      const opusError = opus ? errorOf(opus) : null;
      groups.push({
        id: key,
        title,
        sample: title,
        createdAt,
        jev,
        opus,
        jevModel: jev && typeof jev.model === "string" ? jev.model : "",
        opusModel: opus && typeof opus.model === "string" ? opus.model : "",
        cost: hasCost ? (costs[0] ?? 0) + (costs[1] ?? 0) : null,
        chunks,
        jevError,
        opusError,
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
 * discrepancy), except rows where either side's run failed (`jevError` /
 * `opusError` from `outputs[agentId]._error`) always keep `agree: false`
 * so they stay visible under Discrepancies. `comparable` is false when
 * either side's answers are missing (single-sided pair or a side with no
 * v2 identifier output) — missing-side rows appear only under All, while
 * error rows stay in Discrepancies. Never throws.
 */
export function buildRows(groups: ReviewGroup[]): ReviewRow[] {
  const rows: ReviewRow[] = [];
  try {
    for (const g of groups ?? []) {
      if (!g) continue;
      const firefliesUrl = firefliesOf(g.jev) !== "" ? firefliesOf(g.jev) : firefliesOf(g.opus);
      const sample = g.sample || g.title || "Untitled run";
      const jevError: string | null = g.jevError ?? (g.jev ? errorOf(g.jev) : null);
      const opusError: string | null = g.opusError ?? (g.opus ? errorOf(g.opus) : null);
      const errored = jevError !== null || opusError !== null;
      for (const key of IDENTIFIER_QUESTION_KEYS) {
        try {
          const jev = sideOf(g.jev, key);
          jev.error = jevError;
          const opus = sideOf(g.opus, key);
          opus.error = opusError;
          let question = "";
          try {
            const jevAgent = identifierAgentOf(g.jev);
            const opusAgent = identifierAgentOf(g.opus);
            question = questionText(
              g.jev,
              jevAgent ? jevAgent.id : "agent_identifier",
              key,
            );
            if (question === "") {
              question = questionText(
                g.opus,
                opusAgent ? opusAgent.id : "agent_identifier",
                key,
              );
            }
          } catch {
            question = "";
          }
          const comparable = jev.value !== null && opus.value !== null;
          rows.push({
            key: `${g.id}|${key}`,
            groupId: g.id,
            sample,
            questionKey: key,
            question,
            jev,
            opus,
            agree: errored ? false : jev.value === opus.value,
            comparable,
            firefliesUrl,
            chunks: g.chunks ?? [],
            jevError,
            opusError,
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
// Batches (auto-clustered eval runs, browser-local) + row stars (server-
// persisted via /api/v1/review/stars so they survive browser wipes and
// machines). Batch names/selection stay in frontend-local localStorage;
// stars never touch localStorage. Pure helpers below never throw (async
// server calls throw on failure instead).
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
export const HIDDEN_BATCHES_KEY = "review:hiddenBatches";

export type BatchSelection = {
  /** Selected batch id; null = All batches. */
  batchId: string | null;
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

/**
 * Persisted batch selection; null when nothing stored yet. Tolerates the
 * old `{ batchId, selected }` shape (extra fields ignored) and a bare
 * JSON string batch id.
 */
export function loadBatchSel(): BatchSelection | null {
  try {
    const raw = storageGet(BATCH_SEL_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === "string") return { batchId: parsed };
    if (parsed === null) return { batchId: null };
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const rec = parsed as Record<string, unknown>;
    if (rec.batchId === null || rec.batchId === undefined) return { batchId: null };
    if (typeof rec.batchId === "string") return { batchId: rec.batchId };
    return { batchId: null };
  } catch {
    return null;
  }
}

export function saveBatchSel(sel: BatchSelection): void {
  try {
    storageSet(BATCH_SEL_KEY, JSON.stringify({ batchId: sel.batchId ?? null }));
  } catch {
    // ignore
  }
}

/** Batch ids the user hid from the chips (reversible, data untouched). Never throws. */
export function loadHiddenBatches(): string[] {
  try {
    const raw = storageGet(HIDDEN_BATCHES_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === "string" && v !== "");
  } catch {
    return [];
  }
}

export function saveHiddenBatches(ids: string[]): void {
  try {
    const clean = (ids ?? []).filter((v) => typeof v === "string" && v !== "");
    storageSet(HIDDEN_BATCHES_KEY, JSON.stringify(clean));
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

/** True when either side's run failed (error rows stay in Discrepancies). */
export function isErrorRow(row: Pick<ReviewRow, "jevError" | "opusError">): boolean {
  try {
    return row.jevError !== null || row.opusError !== null;
  } catch {
    return false;
  }
}

/**
 * One-time star-key remap after the pair-id change: old stars are keyed
 * `run_group_id|qkey`, new pair ids are `meeting:<meeting_id>|qkey`. For
 * each stored key whose group part equals a `run_group_id` present in the
 * current logs, rewrite it to the corresponding `meeting:<meeting_id>` key
 * (skip when that log has no meeting_id). Keys already on `meeting:` ids,
 * keys without a `|` separator, and anything unmapped (e.g. batch-name
 * keys) pass through untouched. De-duplicates preserving order. Never
 * throws.
 */
export function remapStars(stars: string[], logs: LogRow[]): string[] {
  try {
    const map = new Map<string, string>();
    for (const log of logs ?? []) {
      try {
        if (!log || typeof log !== "object") continue;
        const rg = typeof log.run_group_id === "string" ? log.run_group_id.trim() : "";
        if (rg === "" || map.has(rg)) continue;
        const m = meetingIdOf(log);
        if (m === "") continue;
        map.set(rg, `meeting:${m}`);
      } catch {
        // ignore malformed logs, keep the rest
      }
    }
    const out: string[] = [];
    const seen = new Set<string>();
    for (const s of stars ?? []) {
      try {
        let next = s;
        if (typeof s === "string") {
          const i = s.lastIndexOf("|");
          if (i > 0) {
            const g = s.slice(0, i);
            if (!g.startsWith("meeting:")) {
              const m = map.get(g);
              if (m !== undefined) next = `${m}${s.slice(i)}`;
            }
          }
        }
        if (typeof next === "string" && !seen.has(next)) {
          seen.add(next);
          out.push(next);
        }
      } catch {
        // ignore malformed keys, keep the rest
      }
    }
    return out;
  } catch {
    return stars ?? [];
  }
}

/** Starred row keys (`${groupId}|${questionKey}`), server-persisted. GET
 * failure (or a malformed payload) throws for the caller to toast. */
export async function fetchStars(): Promise<string[]> {
  const res = await api.get("/review/stars");
  const data: unknown = res.data;
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("bad stars response");
  }
  const list = (data as { stars?: unknown }).stars;
  if (!Array.isArray(list)) throw new Error("bad stars response");
  return list.filter((s): s is string => typeof s === "string");
}

/** PUT the whole star array; failure throws for the caller to toast + roll
 * back. Non-string entries are dropped client-side (the server also
 * ignores, de-duplicates and sorts them). */
export async function persistStars(stars: string[]): Promise<void> {
  const clean = (stars ?? []).filter((s) => typeof s === "string");
  await api.put("/review/stars", { stars: clean });
}
