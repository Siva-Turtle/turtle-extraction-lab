import * as React from "react";
import { Pencil } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import {
  IDENTIFIER_QUESTION_KEYS,
  fillableAttributesOf,
  formatValue,
  identifierAnswersOf,
  selectedAgentsOf,
} from "../../lib/format";
import type { LogRow } from "../../lib/logTypes";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { CardTitle } from "../ui/Card";
import { ThumbButtons } from "../ui/ThumbButtons";
import { ReusedBadge, isReused, reusedFromLabel } from "./ReusedBadge";
import { UnwrappedListTable } from "./UnwrappedListTable";

type PrettyAttr = {
  value?: unknown;
  confidence?: unknown;
  confidence_type?: unknown;
  evidence?: unknown;
  /** Raw entry when the attribute is unwrapped (no wrapper). */
  raw?: unknown;
  unwrapped?: boolean;
};

function snapshotWrapResult(log: LogRow, agentId: string, attr: string): boolean | undefined {
  try {
    const snap = (log.agent_snapshot ?? {})[agentId] as
      | { attributes?: { name?: unknown; wrap_result?: unknown }[] }
      | undefined;
    const attrs = snap?.attributes;
    if (!Array.isArray(attrs)) {
      const attrSnap = (log.attribute_snapshot ?? {}) as Record<string, unknown>;
      const byAgent = attrSnap[agentId] as
        | { attributes?: { name?: unknown; wrap_result?: unknown }[] }
        | undefined;
      if (byAgent && Array.isArray(byAgent.attributes)) {
        for (const a of byAgent.attributes) {
          if (a && typeof a === "object" && (a as { name?: unknown }).name === attr) {
            const w = (a as { wrap_result?: unknown }).wrap_result;
            if (typeof w === "boolean") return w;
          }
        }
      }
      return undefined;
    }
    for (const a of attrs) {
      if (a && typeof a === "object" && (a as { name?: unknown }).name === attr) {
        const w = (a as { wrap_result?: unknown }).wrap_result;
        if (typeof w === "boolean") return w;
      }
    }
  } catch {
    // ignore
  }
  return undefined;
}

function isUnwrappedEntry(log: LogRow, agentId: string, attr: string, raw: unknown): boolean {
  const w = snapshotWrapResult(log, agentId, attr);
  if (typeof w === "boolean") return w === false;
  return Array.isArray(raw);
}

function prettyEntries(
  out: unknown,
  log?: LogRow,
  agentId?: string,
): [string, PrettyAttr][] {
  if (!out || typeof out !== "object") return [];
  return (Object.entries(out as Record<string, unknown>) as [string, unknown][])
    .filter(([k]) => k !== "_error")
    .map(([k, v]) => {
      if (Array.isArray(v)) {
        const unwrapped =
          log && agentId ? isUnwrappedEntry(log, agentId, k, v) : true;
        if (unwrapped) {
          return [k, { raw: v, unwrapped: true } as PrettyAttr];
        }
        return [k, { value: v } as PrettyAttr];
      }
      if (v && typeof v === "object" && !Array.isArray(v) && "value" in (v as Record<string, unknown>)) {
        return [k, v as PrettyAttr];
      }
      if (Array.isArray(v)) {
        return [k, { value: v } as PrettyAttr];
      }
      // Raw object without wrapper (should not happen for wrapped, but keep).
      if (v && typeof v === "object") {
        const maybeUnwrapped = log && agentId ? isUnwrappedEntry(log, agentId, k, v) : false;
        if (maybeUnwrapped) {
          return [k, { raw: v, unwrapped: true } as PrettyAttr];
        }
      }
      return [k, (v && typeof v === "object" ? v : {}) as PrettyAttr];
    });
}

/** Per-agent reuse info from usage.per_agent (null when fresh/unknown). */
function agentReuse(log: LogRow, agentId: string): { createdAt: string | null } | null {
  try {
    const perAgent = log.usage?.per_agent?.[agentId];
    if (!perAgent || typeof perAgent !== "object") return null;
    const id = (perAgent as { reused_from_log_id?: unknown }).reused_from_log_id;
    if (typeof id !== "string" || id === "") return null;
    const ts = (perAgent as { reused_from_created_at?: unknown }).reused_from_created_at;
    return { createdAt: typeof ts === "string" ? ts : null };
  } catch {
    return null;
  }
}

/** Agent card title with Reused + __agent__ auto badges. */
function AgentTitle({ log, agentId, name }: { log: LogRow; agentId: string; name: string }) {
  const reused = agentReuse(log, agentId);
  const autoCell = savedFeedback(log, name, "__agent__");
  const showAuto = autoCell !== null && autoCell.rating !== "";
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <CardTitle className="min-w-0">{name}</CardTitle>
      {reused && <ReusedBadge fromCreatedAt={reused.createdAt} />}
      {showAuto && (
        <span
          title={autoCell.remarks || undefined}
          className="inline-flex items-center gap-1 rounded-full border border-[#e5e7eb] px-1.5 py-0.5 font-sans text-[10px] font-bold text-[#4a5058] dark:border-white/10 dark:text-[#C3C2B7]"
        >
          <span aria-hidden="true">{autoCell.rating === "up" ? "👍" : "👎"}</span>
          Auto
        </span>
      )}
    </div>
  );
}

/** Display name for an agent id; falls back to the id when the snapshot is missing/malformed. */
function agentDisplayName(log: LogRow, agentId: string): string {
  const snap = log.agent_snapshot?.[agentId];
  if (snap && typeof snap === "object" && typeof snap.name === "string" && snap.name.trim() !== "") {
    return snap.name;
  }
  return agentId;
}

/** Saved feedback for one agent/attribute; null when missing or malformed. Never throws. */
function savedFeedback(
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

function AutoTag(): React.JSX.Element {
  return (
    <span
      title="Automatically flagged: identifier listed it but the agent returned nothing, or vice versa"
      className="rounded-full border border-[#e5e7eb] px-1.5 py-0.5 font-sans text-[10px] font-bold text-[#4a5058] dark:border-white/10 dark:text-[#C3C2B7]"
    >
      Auto
    </span>
  );
}

/** Consistency line: v2 (status + reasons) or old (missed/unexpected). */
function ConsistencyLine({
  log,
  agentId,
}: {
  log: LogRow;
  agentId: string;
}): React.JSX.Element | null {
  const entry = log.consistency?.agents?.[agentId];
  if (!entry) return null;
  const isV2 = (log.consistency as { version?: unknown } | null | undefined)?.version === 2;
  if (isV2) {
    const status = typeof entry.status === "string" ? entry.status : "";
    const reasons = Array.isArray(entry.reasons) ? entry.reasons : [];
    const label =
      status === "hit"
        ? "hit"
        : status === "miss"
          ? "miss"
          : status === "error"
            ? "error"
            : status === "not_scored"
              ? (reasons.includes("always") ? "always" : "not scored")
              : status || "—";
    const parts: string[] = [label];
    if (reasons.length > 0) parts.push(reasons.join(", "));
    if (typeof entry.score === "number" && Number.isFinite(entry.score)) {
      parts.push(`${Math.round(entry.score * 100)}%`);
    }
    return (
      <p
        className="mt-1.5 font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]"
        title="Auto routing: identifier answers vs what the agent extracted"
      >
        {parts.join(" · ")}
      </p>
    );
  }
  const score = typeof entry.score === "number" ? Math.round(entry.score * 100) : null;
  const missed = (entry.missed ?? []).filter((s) => s.trim() !== "");
  const unexpected = (entry.unexpected ?? []).filter((s) => s.trim() !== "");
  const parts: string[] = [score === null ? "Consistency —" : `Consistency ${score}%`];
  if (missed.length > 0) parts.push(`missed: ${missed.join(", ")}`);
  if (unexpected.length > 0) parts.push(`unexpected: ${unexpected.join(", ")}`);
  return (
    <p
      className="mt-1.5 font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]"
      title="Identifier's predicted attributes vs what the agents actually extracted"
    >
      {parts.join(" · ")}
    </p>
  );
}

function IdentifierFeedback({
  log,
  agentName,
  saved,
  disabled,
}: {
  log: LogRow;
  agentName: string;
  saved: { rating: string; remarks: string; auto?: boolean } | null;
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const [rating, setRating] = React.useState<"up" | "down" | null>(
    saved?.rating === "up" ? "up" : saved?.rating === "down" ? "down" : null,
  );
  const [remarksText, setRemarksText] = React.useState(
    typeof saved?.remarks === "string" ? saved.remarks : "",
  );
  const [editing, setEditing] = React.useState(false);

  const save = useMutation({
    mutationFn: async (vars: { rating: "up" | "down" | null; remarks: string }) =>
      (
        await api.post(`/runs/${log.run_id}/feedback`, {
          agent_name: agentName,
          attribute_name: "selected_agents",
          rating: vars.rating,
          remarks: vars.remarks,
        })
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["logs"] });
      qc.invalidateQueries({ queryKey: ["logs-all"] });
      qc.invalidateQueries({ queryKey: ["log-group"] });
      toast.success("Rating updated");
    },
    onError: () => toast.error("Could not save rating"),
  });

  function handleThumb(next: "up" | "down" | null) {
    if (disabled) return;
    if (next === null) return;
    setRating(next);
    save.mutate({ rating: next, remarks: remarksText });
  }

  function handleRemarksSave() {
    if (disabled) return;
    if (!rating) {
      toast.error("Pick 👍 or 👎 first");
      return;
    }
    save.mutate({ rating, remarks: remarksText }, { onSuccess: () => setEditing(false) });
  }

  return (
    <div className="mt-2 flex max-w-full flex-wrap items-center gap-2">
      <ThumbButtons value={rating} onChange={handleThumb} disabled={disabled} />
      {saved?.auto === true && <AutoTag />}
      <span
        className={cn(
          "min-w-0 max-w-60 flex-1 truncate font-sans text-xs",
          remarksText ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98]",
        )}
        title={remarksText || undefined}
      >
        {remarksText || "—"}
      </span>
      <button
        type="button"
        onClick={() => setEditing((e) => !e)}
        aria-label={`Edit remarks for ${agentName} / selected_agents`}
        className="flex h-6 w-6 items-center justify-center rounded-full text-[#4a5058] transition-colors hover:bg-[#f1f2f3] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]"
      >
        <Pencil className="h-3 w-3" aria-hidden="true" />
      </button>
      {editing && (
        <div className="flex w-full flex-wrap items-center gap-2">
          <input
            value={remarksText}
            onChange={(e) => setRemarksText(e.target.value)}
            placeholder="Remarks…"
            aria-label="Remarks"
            className="h-8 min-w-40 flex-1 rounded-lg border border-[#e5e7eb] bg-white px-2 font-sans text-xs text-[#1d1d1d] placeholder:text-[#8a8f98] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC]"
          />
          <Button variant="secondary" size="sm" loading={save.isPending} onClick={handleRemarksSave}>
            Save
          </Button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="px-1 font-heading text-xs font-bold text-[#4a5058] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

function AttrRow({
  log,
  agentName,
  attr,
  r,
  saved,
  sn,
  disabled,
}: {
  log: LogRow;
  agentName: string;
  attr: string;
  r: PrettyAttr;
  saved: { rating: string; remarks: string; auto?: boolean } | null;
  sn: number;
  disabled?: boolean;
}) {
  const qc = useQueryClient();
  const [rating, setRating] = React.useState<"up" | "down" | null>(
    saved?.rating === "up" ? "up" : saved?.rating === "down" ? "down" : null,
  );
  const [remarksText, setRemarksText] = React.useState(
    typeof saved?.remarks === "string" ? saved.remarks : "",
  );
  const [editingRemarks, setEditingRemarks] = React.useState(false);

  const save = useMutation({
    mutationFn: async (vars: { rating: "up" | "down" | null; remarks: string }) =>
      (
        await api.post(`/runs/${log.run_id}/feedback`, {
          agent_name: agentName,
          attribute_name: attr,
          rating: vars.rating,
          remarks: vars.remarks,
        })
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["logs"] });
      qc.invalidateQueries({ queryKey: ["logs-all"] });
      qc.invalidateQueries({ queryKey: ["log-group"] });
      toast.success("Rating updated");
    },
    onError: () => toast.error("Could not save rating"),
  });

  function handleThumb(next: "up" | "down" | null) {
    if (disabled) return;
    if (next === null) return;
    setRating(next);
    save.mutate({ rating: next, remarks: remarksText });
  }

  function handleRemarksSave() {
    if (disabled) return;
    if (!rating) {
      toast.error("Pick 👍 or 👎 first");
      return;
    }
    save.mutate({ rating, remarks: remarksText }, { onSuccess: () => setEditingRemarks(false) });
  }

  const unwrapped = (r as PrettyAttr).unwrapped === true;
  const rawList = unwrapped ? (r as PrettyAttr).raw : undefined;
  const valueText = unwrapped ? formatValue(rawList) : formatValue(r.value);
  const evidenceText = typeof r.evidence === "string" ? r.evidence : "";

  if (unwrapped) {
    return (
      <tr className="border-t border-[#e5e7eb] text-[#1d1d1d] dark:border-white/10 dark:text-[#F0EFEC]">
        <td className="break-words px-3 py-2 font-mono text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]">
          {sn}
        </td>
        <td className="break-words px-3 py-2 font-heading font-bold [overflow-wrap:anywhere]">{attr}</td>
        <td className="break-words px-3 py-2 [overflow-wrap:anywhere]" colSpan={3}>
          <UnwrappedListTable value={rawList} />
        </td>
        <td className="break-words px-3 py-2 [overflow-wrap:anywhere]">
          <span className="italic text-[#8a8f98]">—</span>
        </td>
        <td className="break-words px-3 py-2 [overflow-wrap:anywhere]">
          <div className="flex flex-wrap items-center gap-1.5">
            <ThumbButtons value={rating} onChange={handleThumb} disabled={disabled} />
            {saved?.auto === true && <AutoTag />}
          </div>
        </td>
        <td className="break-words px-3 py-2 [overflow-wrap:anywhere]">
          <div className="flex min-w-0 flex-wrap items-start gap-1.5">
            <span
              className={cn(
                "block min-w-0 flex-1 break-words [overflow-wrap:anywhere]",
                remarksText ? "" : "text-[#8a8f98]",
              )}
              title={remarksText || undefined}
            >
              {remarksText || "—"}
            </span>
            <button
              type="button"
              onClick={() => setEditingRemarks((e) => !e)}
              aria-label={`Edit remarks for ${agentName} / ${attr}`}
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[#4a5058] transition-colors hover:bg-[#f1f2f3] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]"
            >
              <Pencil className="h-3 w-3" aria-hidden="true" />
            </button>
          </div>
          {editingRemarks && (
            <div className="mt-1.5 grid gap-1.5">
              <input
                value={remarksText}
                onChange={(e) => setRemarksText(e.target.value)}
                placeholder="Remarks…"
                aria-label="Remarks"
                className="h-8 w-full min-w-0 rounded-lg border border-[#e5e7eb] bg-white px-2 font-sans text-xs text-[#1d1d1d] placeholder:text-[#8a8f98] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC]"
              />
              <div className="flex gap-1.5">
                <Button variant="secondary" size="sm" loading={save.isPending} onClick={handleRemarksSave}>
                  Save
                </Button>
                <button
                  type="button"
                  onClick={() => setEditingRemarks(false)}
                  className="px-1 font-heading text-xs font-bold text-[#4a5058] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </td>
      </tr>
    );
  }

  return (
    <tr className="border-t border-[#e5e7eb] text-[#1d1d1d] dark:border-white/10 dark:text-[#F0EFEC]">
      <td className="break-words px-3 py-2 font-mono text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]">
        {sn}
      </td>
      <td className="break-words px-3 py-2 font-heading font-bold [overflow-wrap:anywhere]">{attr}</td>
      <td className="break-words px-3 py-2 [overflow-wrap:anywhere]" title={valueText}>
        {valueText}
      </td>
      <td className="break-words px-3 py-2 font-mono [overflow-wrap:anywhere]">
        {typeof r.confidence === "number" ? r.confidence.toFixed(2) : "?"}
      </td>
      <td className="break-words px-3 py-2 [overflow-wrap:anywhere]">
        <Badge tone="brand">{String(r.confidence_type ?? "?")}</Badge>
      </td>
      <td className="break-words px-3 py-2 [overflow-wrap:anywhere]">
        {evidenceText !== "" ? (
          <span
            className="break-words italic text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]"
            title={evidenceText}
          >
            “{evidenceText}”
          </span>
        ) : (
          "—"
        )}
      </td>
      <td className="break-words px-3 py-2 [overflow-wrap:anywhere]">
        <div className="flex flex-wrap items-center gap-1.5">
          <ThumbButtons value={rating} onChange={handleThumb} disabled={disabled} />
          {saved?.auto === true && <AutoTag />}
        </div>
      </td>
      <td className="break-words px-3 py-2 [overflow-wrap:anywhere]">
        <div className="flex min-w-0 flex-wrap items-start gap-1.5">
          <span
            className={cn(
              "block min-w-0 flex-1 break-words [overflow-wrap:anywhere]",
              remarksText ? "" : "text-[#8a8f98]",
            )}
            title={remarksText || undefined}
          >
            {remarksText || "—"}
          </span>
          <button
            type="button"
            onClick={() => setEditingRemarks((e) => !e)}
            aria-label={`Edit remarks for ${agentName} / ${attr}`}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[#4a5058] transition-colors hover:bg-[#f1f2f3] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]"
          >
            <Pencil className="h-3 w-3" aria-hidden="true" />
          </button>
        </div>
        {editingRemarks && (
          <div className="mt-1.5 grid gap-1.5">
            <input
              value={remarksText}
              onChange={(e) => setRemarksText(e.target.value)}
              placeholder="Remarks…"
              aria-label="Remarks"
              className="h-8 w-full min-w-0 rounded-lg border border-[#e5e7eb] bg-white px-2 font-sans text-xs text-[#1d1d1d] placeholder:text-[#8a8f98] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC]"
            />
            <div className="flex gap-1.5">
              <Button variant="secondary" size="sm" loading={save.isPending} onClick={handleRemarksSave}>
                Save
              </Button>
              <button
                type="button"
                onClick={() => setEditingRemarks(false)}
                className="px-1 font-heading text-xs font-bold text-[#4a5058] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </td>
    </tr>
  );
}

/**
 * Today's single-model per-agent table (extracted from Logs.tsx PrettyPanel;
 * markup and classes unchanged). One column => this table. Supports per-agent
 * retry (`onRetryAgent`) with `runningAgents` (`Record<`${colKey}|${agentId}`, true>`
 * or `Record<agentId, true>`; `colKey` scopes the lookup when provided) and
 * shows consistency + Auto tags for auto-select runs. Streaming provisional
 * logs (snapshot lists agents missing from outputs) render those agents as
 * muted "running…" cards; thumbs stay disabled via `disabled` until the
 * final log arrives.
 */
export function SingleModelTable({
  log,
  onRetryAgent,
  runningAgents,
  colKey,
  disabled,
}: {
  log: LogRow;
  onRetryAgent?: (agentId: string) => void;
  runningAgents?: Record<string, boolean>;
  colKey?: string;
  disabled?: boolean;
}): React.JSX.Element {
  const reused = isReused(log);
  const reusedWhen = reused ? reusedFromLabel(log) : null;

  function isRunning(agentId: string): boolean {
    if (!runningAgents) return false;
    if (colKey && runningAgents[`${colKey}|${agentId}`] === true) return true;
    return runningAgents[agentId] === true;
  }

  return (
    <div className="grid min-w-0 max-w-full gap-3">
      {reused && (
        <p className="flex flex-wrap items-center gap-2 font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
          <ReusedBadge log={log} />
          <span>
            Reused output from {reusedWhen ?? "a previous run"} — no new model call.
          </span>
        </p>
      )}
      {Object.entries(log.outputs ?? {}).map(([agentId, out]) => {
        const agentName = agentDisplayName(log, agentId);
        if (out && typeof out === "object" && "_error" in (out as Record<string, unknown>)) {
          const err = (out as Record<string, unknown>)._error;
          const running = isRunning(agentId);
          return (
            <div
              key={agentId}
              className="min-w-0 max-w-full rounded-2xl border border-[#ef4444]/40 bg-[#fdecec] p-4 dark:bg-[#ef4444]/10"
            >
              <AgentTitle log={log} agentId={agentId} name={agentName} />
              <p className="mt-1 break-words font-sans text-sm text-[#b91c1c] dark:text-[#f87171]">
                {running ? "Retrying agent…" : `Agent failed: ${String(err)}`}
              </p>
              {!running && onRetryAgent && (
                <div className="mt-2">
                  <Button variant="secondary" size="sm" onClick={() => onRetryAgent(agentId)}>
                    Retry
                  </Button>
                </div>
              )}
            </div>
          );
        }
        const selected = selectedAgentsOf(out);
        if (selected !== null) {
          const saved = savedFeedback(log, agentName, "selected_agents");
          const fillable = fillableAttributesOf(out);
          return (
            <div key={agentId} className="min-w-0 max-w-full rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
              <AgentTitle log={log} agentId={agentId} name={agentName} />
              <div className="mt-2 grid min-w-0 max-w-full gap-2">
                {selected.length === 0 ? (
                  <p className="font-heading text-xs text-[#8a8f98]">No agents selected.</p>
                ) : (
                  <div className="grid gap-1.5">
                    {selected.map((name) => {
                      const attrs = fillable[name] ?? [];
                      return (
                        <div key={name} className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <Badge tone="brand">{name}</Badge>
                          {attrs.length > 0 && (
                            <>
                              <span className="flex min-w-0 flex-wrap gap-1">
                                {attrs.map((attr, i) => (
                                  <span
                                    key={`${attr}-${i}`}
                                    className="inline-flex items-center break-words rounded-full bg-[#f1f2f3] px-2 py-0.5 font-sans text-[11px] text-[#4a5058] dark:bg-white/10 dark:text-[#C3C2B7]"
                                  >
                                    {attr}
                                  </span>
                                ))}
                              </span>
                              <span className="font-sans text-[11px] text-[#8a8f98] dark:text-[#C3C2B7]">
                                {attrs.length} attribute{attrs.length === 1 ? "" : "s"}
                              </span>
                            </>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
              <IdentifierFeedback
                log={log}
                agentName={agentName}
                saved={saved}
                disabled={disabled}
              />
            </div>
          );
        }
        const answers = identifierAnswersOf(out);
        if (answers !== null) {
          return (
            <div key={agentId} className="min-w-0 max-w-full rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
              <AgentTitle log={log} agentId={agentId} name={agentName} />
              <div className="mt-2 grid gap-1.5">
                {IDENTIFIER_QUESTION_KEYS.map((k) => {
                  const yes = answers[k] === true;
                  return (
                    <div key={k} className="flex min-w-0 flex-wrap items-center gap-1.5">
                      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]" title={k}>
                        {k}
                      </span>
                      <Badge tone={yes ? "success" : "neutral"}>{yes ? "Yes" : "No"}</Badge>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        }
        const entries = prettyEntries(out, log, agentId);
        return (
          <div key={agentId} className="min-w-0 max-w-full rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
            <AgentTitle log={log} agentId={agentId} name={agentName} />
            <ConsistencyLine log={log} agentId={agentId} />
            {entries.length === 0 ? (
              <p className="mt-2 font-heading text-xs text-[#8a8f98]">Agent returned no attributes.</p>
            ) : (
              <div className="mt-1.5 overflow-x-hidden rounded-xl border border-[#e5e7eb] dark:border-white/10">
                <table className="w-full table-fixed font-sans text-xs">
                  <colgroup>
                    <col style={{ width: "5%" }} />
                    <col style={{ width: "18%" }} />
                    <col style={{ width: "32%" }} />
                    <col style={{ width: "7%" }} />
                    <col style={{ width: "10%" }} />
                    <col style={{ width: "10%" }} />
                    <col style={{ width: "8%" }} />
                    <col style={{ width: "10%" }} />
                  </colgroup>
                  <thead>
                    <tr className="bg-[#f1f2f3] text-left font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:bg-white/5 dark:text-[#C3C2B7]">
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">SN</th>
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">Attribute</th>
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">Value</th>
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">Conf.</th>
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">Conf. type</th>
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">Evidence</th>
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">Feedback</th>
                      <th className="break-words px-3 py-2 [overflow-wrap:anywhere]">Remark</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map(([attr, r], idx) => (
                      <AttrRow
                        key={attr}
                        log={log}
                        agentName={agentName}
                        attr={attr}
                        r={r}
                        saved={savedFeedback(log, agentName, attr)}
                        sn={idx + 1}
                        disabled={disabled}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
      {(() => {
        // Streaming provisional: agents in the snapshot with no output yet
        // are still running — muted placeholder (agent list from start).
        try {
          const outs = (log.outputs ?? {}) as Record<string, unknown>;
          const snap = (log.agent_snapshot ?? {}) as Record<string, { name?: unknown }>;
          const pending = Object.keys(snap).filter((id) => !(id in outs));
          if (pending.length === 0) return null;
          return pending.map((agentId) => {
            const raw = snap[agentId];
            const name =
              raw && typeof raw.name === "string" && raw.name.trim() !== ""
                ? raw.name
                : agentId;
            return (
              <div
                key={`pending-${agentId}`}
                className="min-w-0 max-w-full rounded-2xl border border-dashed border-[#e5e7eb] p-4 dark:border-white/10"
              >
                <p className="font-heading text-xs font-bold text-[#4a5058] dark:text-[#C3C2B7]">
                  {name}
                </p>
                <p className="mt-1 font-sans text-xs italic text-[#8a8f98]">running…</p>
              </div>
            );
          });
        } catch {
          return null;
        }
      })()}
    </div>
  );
}
