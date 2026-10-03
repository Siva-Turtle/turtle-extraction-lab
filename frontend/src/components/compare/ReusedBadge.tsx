import { fmt } from "../../lib/format";
import type { LogRow } from "../../lib/logTypes";
import { Badge } from "../ui/Badge";

/** True when the log row is a reused copy (no new model call). */
export function isReused(log: LogRow | null | undefined): boolean {
  return (
    !!log &&
    typeof log.reused_from_log_id === "string" &&
    log.reused_from_log_id !== ""
  );
}

/** Formatted date/time of the original run, or null when unknown. */
export function reusedFromLabel(log: LogRow): string | null {
  const ts = log.reused_from_created_at;
  if (typeof ts !== "string" || ts.trim() === "") return null;
  try {
    return fmt(ts);
  } catch {
    return ts;
  }
}

/** Format an ISO timestamp for the reused title, or null when unknown. */
export function formatReusedWhen(ts: string | null | undefined): string | null {
  if (typeof ts !== "string" || ts.trim() === "") return null;
  try {
    return fmt(ts);
  } catch {
    return ts;
  }
}

/**
 * Per-agent reuse counts for a log: how many per_agent entries carry
 * `reused_from_log_id`, out of how many total. Falls back to
 * `usage.reused_agents` (with the agent snapshot size as total) when
 * per_agent is missing, so old shapes still degrade gracefully.
 */
export function reusedCounts(log: LogRow | null | undefined): {
  reused: number;
  total: number;
} {
  if (!log || typeof log !== "object") return { reused: 0, total: 0 };
  const usage = (log as LogRow).usage;
  const perAgent =
    usage && typeof usage === "object"
      ? (usage as { per_agent?: unknown }).per_agent
      : undefined;
  if (perAgent && typeof perAgent === "object" && !Array.isArray(perAgent)) {
    const entries = Object.entries(perAgent as Record<string, unknown>);
    if (entries.length > 0) {
      let reused = 0;
      for (const [, v] of entries) {
        if (
          v &&
          typeof v === "object" &&
          !Array.isArray(v) &&
          typeof (v as { reused_from_log_id?: unknown }).reused_from_log_id ===
            "string" &&
          ((v as { reused_from_log_id: string }).reused_from_log_id !== "")
        ) {
          reused += 1;
        }
      }
      return { reused, total: entries.length };
    }
  }
  const reusedAgents =
    usage && typeof usage === "object"
      ? (usage as { reused_agents?: unknown }).reused_agents
      : undefined;
  if (reusedAgents && typeof reusedAgents === "object" && !Array.isArray(reusedAgents)) {
    const keys = Object.keys(reusedAgents as Record<string, unknown>);
    if (keys.length > 0) {
      const snap = (log as LogRow).agent_snapshot;
      const total =
        snap && typeof snap === "object"
          ? Object.keys(snap as Record<string, unknown>).length
          : keys.length;
      return { reused: keys.length, total: total > 0 ? total : keys.length };
    }
  }
  return { reused: 0, total: 0 };
}

/** True when at least one (but not necessarily all) agent was reused. */
export function isPartiallyReused(log: LogRow | null | undefined): boolean {
  const { reused } = reusedCounts(log);
  return reused > 0 && !isReused(log);
}

/** True when every agent was reused (log-level flag or all per-agent flags). */
export function isFullyReused(log: LogRow | null | undefined): boolean {
  if (isReused(log)) return true;
  const { reused, total } = reusedCounts(log);
  return total > 0 && reused === total;
}

/**
 * Small shared "Reused" marker: muted/amber badge with a title explaining
 * the output was copied (cost/time are from the original run). Pass
 * `fromCreatedAt` for a per-agent badge (original date of that agent).
 */
export function ReusedBadge({
  log,
  fromCreatedAt,
}: {
  log?: LogRow | null;
  fromCreatedAt?: string | null;
}): React.JSX.Element {
  const when =
    typeof fromCreatedAt === "string" && fromCreatedAt.trim() !== ""
      ? formatReusedWhen(fromCreatedAt)
      : log
        ? reusedFromLabel(log)
        : null;
  const title = when
    ? `Output copied from a run on ${when} — no new model call; cost and time shown are from that run`
    : "Output copied from a previous run — no new model call; cost and time shown are from that run";
  return (
    <span title={title}>
      <Badge tone="warning">Reused</Badge>
    </span>
  );
}
