// Shared mapping from stored log rows to comparison-matrix inputs.
// Used by both Test Lab (finished columns) and the Logs group detail.

import type { CompareAgent, CompareColumn, LogRow } from "./logTypes";

/** done when no agent failed, partial when some did, error when all did. */
function statusOf(log: LogRow): CompareColumn["status"] {
  const outputs = (log.outputs ?? {}) as Record<string, unknown>;
  const entries = Object.values(outputs);
  if (entries.length === 0) return "done";
  let failed = 0;
  for (const out of entries) {
    if (out && typeof out === "object" && !Array.isArray(out) && "_error" in out) failed += 1;
  }
  if (failed === 0) return "done";
  if (failed === entries.length) return "error";
  return "partial";
}

/**
 * One finished matrix column per model+effort, in first-seen order. A retried
 * model adds another log row to the same group; the matrix keeps only the
 * LATEST row (by created_at) per `model|reasoning_effort`. Callers that need
 * every attempt (Raw tab, list badges, cost sums) use the raw rows directly.
 */
export function columnsFromLogs(logs: LogRow[]): CompareColumn[] {
  const latest = new Map<string, LogRow>();
  const order: string[] = [];
  for (const log of logs) {
    const model = typeof log.model === "string" ? log.model : "";
    const effort = typeof log.reasoning_effort === "string" ? log.reasoning_effort : "";
    const provider = typeof log.provider === "string" ? log.provider : "";
    const k = `${model}|${effort}|${provider}`;
    if (!latest.has(k)) order.push(k);
    const cur = latest.get(k);
    // Later in input order wins ties (invalid timestamps sink to -Infinity).
    if (!cur || timeOf(log.created_at) >= timeOf(cur.created_at)) latest.set(k, log);
  }
  return order.map((k) => {
    const log = latest.get(k) as LogRow;
    return {
      key: log.id,
      model: typeof log.model === "string" ? log.model : "",
      effort: typeof log.reasoning_effort === "string" ? log.reasoning_effort : "",
      provider: typeof log.provider === "string" ? log.provider : "",
      status: statusOf(log),
      log,
    };
  });
}

/** Sortable timestamp; invalid timestamps sink last. */
function timeOf(ts: string): number {
  const t = new Date(ts).getTime();
  return Number.isNaN(t) ? Number.NEGATIVE_INFINITY : t;
}

/**
 * Union of compare agents across logs (auto-select: each model may select a
 * different subset). Merged by agent id; attributes merged by name
 * (first-seen order). Never throws.
 */
export function unionAgentsFromLogs(logs: LogRow[]): CompareAgent[] {
  const byId = new Map<string, CompareAgent>();
  for (const log of logs) {
    for (const a of agentsFromLog(log)) {
      const existing = byId.get(a.id);
      if (!existing) {
        byId.set(a.id, {
          id: a.id,
          name: a.name,
          kind: a.kind,
          attributes: [...a.attributes],
        });
      } else {
        const seen = new Set(existing.attributes.map((x) => x.name));
        for (const attr of a.attributes) {
          if (!seen.has(attr.name)) {
            seen.add(attr.name);
            existing.attributes.push(attr);
          }
        }
      }
    }
  }
  return [...byId.values()];
}

/**
 * Compare agents from one log's `agent_snapshot` (real shape: per agent id
 * {name, kind, attributes [{name, type, description, group}]}). Never throws;
 * [] when the snapshot is missing or malformed.
 */
export function agentsFromLog(log: LogRow): CompareAgent[] {
  const snap = log.agent_snapshot;
  if (!snap || typeof snap !== "object") return [];
  const out: CompareAgent[] = [];
  for (const [agentId, raw] of Object.entries(snap)) {
    const s = (raw ?? {}) as {
      name?: unknown;
      kind?: unknown;
      attributes?: unknown;
    };
    const name = typeof s.name === "string" && s.name.trim() !== "" ? s.name : agentId;
    const kind = typeof s.kind === "string" && s.kind.trim() !== "" ? s.kind : "extraction";
    const attrs: CompareAgent["attributes"] = [];
    if (Array.isArray(s.attributes)) {
      for (const a of s.attributes) {
        if (!a || typeof a !== "object") continue;
        const rec = a as Record<string, unknown>;
        const n = typeof rec.name === "string" ? rec.name : "";
        if (n.trim() === "") continue;
        const wrapRaw = (rec as Record<string, unknown>).wrap_result;
        attrs.push({
          name: n,
          type: typeof rec.type === "string" ? rec.type : "",
          description: typeof rec.description === "string" ? rec.description : "",
          group: typeof rec.group === "string" ? rec.group : "",
          ...(typeof wrapRaw === "boolean" ? { wrap_result: wrapRaw } : {}),
        });
      }
    }
    out.push({ id: agentId, name, kind, attributes: attrs });
  }
  return out;
}
