import * as React from "react";
import { Pencil } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { fillableAttributesOf, formatValue, selectedAgentsOf } from "../../lib/format";
import type { LogRow } from "../../lib/logTypes";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { CardTitle } from "../ui/Card";
import { ThumbButtons } from "../ui/ThumbButtons";
import { ReusedBadge, isReused, reusedFromLabel } from "./ReusedBadge";

type PrettyAttr = {
  value?: unknown;
  confidence?: unknown;
  confidence_type?: unknown;
  evidence?: unknown;
};

function prettyEntries(out: unknown): [string, PrettyAttr][] {
  if (!out || typeof out !== "object") return [];
  return (Object.entries(out as Record<string, unknown>) as [string, unknown][])
    .filter(([k]) => k !== "_error")
    .map(([k, v]) => [k, (v && typeof v === "object" ? v : {}) as PrettyAttr]);
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

/** Agent card title with a Reused badge when this agent was reused. */
function AgentTitle({ log, agentId, name }: { log: LogRow; agentId: string; name: string }) {
  const reused = agentReuse(log, agentId);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <CardTitle className="min-w-0">{name}</CardTitle>
      {reused && <ReusedBadge fromCreatedAt={reused.createdAt} />}
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

function IdentifierFeedback({
  log,
  agentName,
  saved,
}: {
  log: LogRow;
  agentName: string;
  saved: { rating: string; remarks: string } | null;
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
    if (next === null) return;
    setRating(next);
    save.mutate({ rating: next, remarks: remarksText });
  }

  function handleRemarksSave() {
    if (!rating) {
      toast.error("Pick 👍 or 👎 first");
      return;
    }
    save.mutate({ rating, remarks: remarksText }, { onSuccess: () => setEditing(false) });
  }

  return (
    <div className="mt-2 flex max-w-full flex-wrap items-center gap-2">
      <ThumbButtons value={rating} onChange={handleThumb} />
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
}: {
  log: LogRow;
  agentName: string;
  attr: string;
  r: PrettyAttr;
  saved: { rating: string; remarks: string } | null;
  sn: number;
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
    if (next === null) return;
    setRating(next);
    save.mutate({ rating: next, remarks: remarksText });
  }

  function handleRemarksSave() {
    if (!rating) {
      toast.error("Pick 👍 or 👎 first");
      return;
    }
    save.mutate({ rating, remarks: remarksText }, { onSuccess: () => setEditingRemarks(false) });
  }

  const valueText = formatValue(r.value);
  const evidenceText = typeof r.evidence === "string" ? r.evidence : "";

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
          <ThumbButtons value={rating} onChange={handleThumb} />
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
 * markup and classes unchanged). One column => this table.
 */
export function SingleModelTable({ log }: { log: LogRow }): React.JSX.Element {
  const reused = isReused(log);
  const reusedWhen = reused ? reusedFromLabel(log) : null;
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
          return (
            <div
              key={agentId}
              className="min-w-0 max-w-full rounded-2xl border border-[#ef4444]/40 bg-[#fdecec] p-4 dark:bg-[#ef4444]/10"
            >
              <AgentTitle log={log} agentId={agentId} name={agentName} />
              <p className="mt-1 break-words font-sans text-sm text-[#b91c1c] dark:text-[#f87171]">
                Agent failed: {String(err)}
              </p>
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
              <IdentifierFeedback log={log} agentName={agentName} saved={saved} />
            </div>
          );
        }
        const entries = prettyEntries(out);
        return (
          <div key={agentId} className="min-w-0 max-w-full rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
            <AgentTitle log={log} agentId={agentId} name={agentName} />
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
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
