import * as React from "react";
import { Pencil } from "lucide-react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "../../lib/api";
import { formatValue, selectedAgentsOf } from "../../lib/format";
import type { LogRow } from "../../lib/logTypes";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { CardTitle } from "../ui/Card";
import { ThumbButtons } from "../ui/ThumbButtons";

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
      <td className="whitespace-nowrap px-3 py-2 font-mono text-[#4a5058] dark:text-[#C3C2B7]">
        {sn}
      </td>
      <td className="max-w-48 break-words px-3 py-2 font-heading font-bold">{attr}</td>
      <td className="max-w-64 break-words px-3 py-2" title={valueText}>
        {valueText}
      </td>
      <td className="whitespace-nowrap px-3 py-2 font-mono">
        {typeof r.confidence === "number" ? r.confidence.toFixed(2) : "?"}
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <Badge tone="brand">{String(r.confidence_type ?? "?")}</Badge>
      </td>
      <td className="max-w-64 break-words px-3 py-2">
        {evidenceText !== "" ? (
          <span
            className="break-words italic text-[#4a5058] dark:text-[#C3C2B7]"
            title={evidenceText}
          >
            “{evidenceText}”
          </span>
        ) : (
          "—"
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-2">
        <div className="flex items-center gap-1.5">
          <ThumbButtons value={rating} onChange={handleThumb} />
        </div>
      </td>
      <td className="min-w-40 max-w-64 px-3 py-2">
        <div className="flex items-start gap-1.5">
          <span
            className={cn(
              "block min-w-0 max-w-40 flex-1 truncate",
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
  return (
    <div className="grid min-w-0 max-w-full gap-3">
      {Object.entries(log.outputs ?? {}).map(([agentId, out]) => {
        const agentName = agentDisplayName(log, agentId);
        if (out && typeof out === "object" && "_error" in (out as Record<string, unknown>)) {
          const err = (out as Record<string, unknown>)._error;
          return (
            <div
              key={agentId}
              className="min-w-0 max-w-full rounded-2xl border border-[#ef4444]/40 bg-[#fdecec] p-4 dark:bg-[#ef4444]/10"
            >
              <CardTitle>{agentName}</CardTitle>
              <p className="mt-1 break-words font-sans text-sm text-[#b91c1c] dark:text-[#f87171]">
                Agent failed: {String(err)}
              </p>
            </div>
          );
        }
        const selected = selectedAgentsOf(out);
        if (selected !== null) {
          const saved = savedFeedback(log, agentName, "selected_agents");
          return (
            <div key={agentId} className="min-w-0 max-w-full rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
              <CardTitle>{agentName}</CardTitle>
              <div className="mt-2 grid min-w-0 max-w-full gap-2">
                {selected.length === 0 ? (
                  <p className="font-heading text-xs text-[#8a8f98]">No agents selected.</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {selected.map((name) => (
                      <Badge key={name} tone="brand">
                        {name}
                      </Badge>
                    ))}
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
            <CardTitle>{agentName}</CardTitle>
            {entries.length === 0 ? (
              <p className="mt-2 font-heading text-xs text-[#8a8f98]">Agent returned no attributes.</p>
            ) : (
              <div className="mt-1.5 overflow-x-auto rounded-xl border border-[#e5e7eb] dark:border-white/10">
                <table className="w-full min-w-[960px] font-sans text-xs">
                  <thead>
                    <tr className="bg-[#f1f2f3] text-left font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:bg-white/5 dark:text-[#C3C2B7]">
                      <th className="px-3 py-2">SN</th>
                      <th className="px-3 py-2">Attribute</th>
                      <th className="px-3 py-2">Value</th>
                      <th className="px-3 py-2">Conf.</th>
                      <th className="px-3 py-2">Conf. type</th>
                      <th className="px-3 py-2">Evidence</th>
                      <th className="px-3 py-2">Feedback</th>
                      <th className="px-3 py-2">Remark</th>
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
