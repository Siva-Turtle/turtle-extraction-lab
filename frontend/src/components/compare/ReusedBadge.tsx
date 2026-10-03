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

/**
 * Small shared "Reused" marker: muted/amber badge with a title explaining
 * the output was copied (cost/time are from the original run).
 */
export function ReusedBadge({ log }: { log: LogRow }): React.JSX.Element {
  const when = reusedFromLabel(log);
  const title = when
    ? `Output copied from a run on ${when} — no new model call; cost and time shown are from that run`
    : "Output copied from a previous run — no new model call; cost and time shown are from that run";
  return (
    <span title={title}>
      <Badge tone="warning">Reused</Badge>
    </span>
  );
}
