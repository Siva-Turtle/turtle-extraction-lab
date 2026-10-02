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
 * One finished matrix column per log row, in input order. Keys are the log
 * ids (unique even when the same model+effort was retried in one group).
 */
export function columnsFromLogs(logs: LogRow[]): CompareColumn[] {
  return logs.map((log) => ({
    key: log.id,
    model: typeof log.model === "string" ? log.model : "",
    effort: typeof log.reasoning_effort === "string" ? log.reasoning_effort : "",
    status: statusOf(log),
    log,
  }));
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
        attrs.push({
          name: n,
          type: typeof rec.type === "string" ? rec.type : "",
          description: typeof rec.description === "string" ? rec.description : "",
          group: typeof rec.group === "string" ? rec.group : "",
        });
      }
    }
    out.push({ id: agentId, name, kind, attributes: attrs });
  }
  return out;
}
