// Grouping for the log history list: one row per multi-model run.
// Group key = run_group_id, or the log's own id on old rows without one.

import { buildRows, groupAgreementPct } from "./compare";
import { agentsFromLog, columnsFromLogs } from "./compareData";
import type { LogRow } from "./logTypes";

export type LogGroup = {
  /** run_group_id, or the single log's id on old rows. */
  key: string;
  /** Member rows, sorted by created_at ascending. */
  rows: LogRow[];
  /** Earliest created_at in the group. */
  createdAt: string;
  /** One model id per member row, in row order. */
  models: string[];
  /** Summed cost_usd, or null when no member reports one. */
  totalCostUsd: number | null;
  /** Max duration_ms, or null when no member reports one. */
  maxDurationMs: number | null;
  /** Summed total_tokens, or null when no member reports one. */
  totalTokens: number | null;
  /** Thumbs-up cells across all member rows. */
  up: number;
  /** Thumbs-down cells across all member rows. */
  down: number;
  /** Mean agreement % across compared rows, or null for single-row groups. */
  agreePct: number | null;
  /** The single reasoning effort, or "mixed" when members differ. */
  effortLabel: string;
};

/** Group key for one log row (matches the backend read rule). */
export function groupKeyOf(log: LogRow): string {
  const g = typeof log.run_group_id === "string" ? log.run_group_id.trim() : "";
  return g !== "" ? g : log.id;
}

function finiteNum(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function timeOf(ts: string): number {
  const t = new Date(ts).getTime();
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t;
}

/**
 * Group rows by groupKeyOf, members sorted by created_at ascending, groups
 * sorted newest-first (by earliest member, matching today's newest-first
 * list). Never throws; malformed feedback/usage degrades to counts of 0
 * and null totals.
 */
export function groupLogs(rows: LogRow[]): LogGroup[] {
  const byKey = new Map<string, LogRow[]>();
  for (const r of rows) {
    const k = groupKeyOf(r);
    const list = byKey.get(k);
    if (list) list.push(r);
    else byKey.set(k, [r]);
  }
  const groups: LogGroup[] = [];
  for (const [key, unsorted] of byKey) {
    const members = [...unsorted].sort((a, b) => timeOf(a.created_at) - timeOf(b.created_at));
    let cost = 0;
    let hasCost = false;
    let tokens = 0;
    let hasTokens = false;
    let maxMs: number | null = null;
    let up = 0;
    let down = 0;
    const efforts = new Set<string>();
    for (const r of members) {
      const u = r.usage;
      const c = finiteNum(u?.cost_usd);
      if (c !== null) {
        cost += c;
        hasCost = true;
      }
      const t = finiteNum(u?.total_tokens);
      if (t !== null) {
        tokens += t;
        hasTokens = true;
      }
      const ms = finiteNum(u?.duration_ms);
      if (ms !== null) maxMs = maxMs === null ? ms : Math.max(maxMs, ms);
      efforts.add(typeof r.reasoning_effort === "string" ? r.reasoning_effort.trim() : "");
      try {
        const fb = r.feedback;
        if (!fb || typeof fb !== "object") continue;
        for (const byAgent of Object.values(fb)) {
          if (!byAgent || typeof byAgent !== "object") continue;
          for (const entry of Object.values(byAgent as Record<string, unknown>)) {
            if (!entry || typeof entry !== "object") continue;
            const rating = (entry as { rating?: unknown }).rating;
            if (rating === "up") up += 1;
            else if (rating === "down") down += 1;
          }
        }
      } catch {
        // ignore malformed feedback on one row
      }
    }
    let agreePct: number | null = null;
    if (members.length >= 2) {
      try {
        // Agreement uses only the latest row per model+effort (matrix
        // columns); cost/tokens above still sum every row (money was spent).
        const first = members[0] as LogRow;
        agreePct = groupAgreementPct(buildRows(agentsFromLog(first), columnsFromLogs(members)));
      } catch {
        agreePct = null;
      }
    }
    const effortList = [...efforts];
    const singleEffort = effortList.length === 1 ? (effortList[0] as string) : null;
    groups.push({
      key,
      rows: members,
      createdAt: (members[0] as LogRow).created_at,
      models: members.map((r) => (typeof r.model === "string" ? r.model : "")),
      totalCostUsd: hasCost ? cost : null,
      maxDurationMs: maxMs,
      totalTokens: hasTokens ? tokens : null,
      up,
      down,
      agreePct,
      effortLabel: singleEffort !== null ? singleEffort || "—" : "mixed",
    });
  }
  groups.sort((a, b) => {
    const ta = new Date(a.createdAt).getTime();
    const tb = new Date(b.createdAt).getTime();
    if (Number.isNaN(ta) && Number.isNaN(tb)) return 0;
    if (Number.isNaN(ta)) return 1;
    if (Number.isNaN(tb)) return -1;
    return tb - ta;
  });
  return groups;
}
