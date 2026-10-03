import * as React from "react";
import { Loader2 } from "lucide-react";
import { fmtMs, formatValue, modelLabel } from "../../lib/format";
import { NOT_FOUND_CANON } from "../../lib/compare";
import type { CompareAgent, CompareColumn } from "../../lib/logTypes";
import { ThumbButtons } from "../ui/ThumbButtons";
import { CompareToolbar } from "./CompareToolbar";
import { AttributeCompareDrawer } from "./AttributeCompareDrawer";
import { agreementMark, useCompareModel } from "./useCompareModel";
import type { CompareRow } from "../../lib/compare";

const PENDING_CANON = "__pending__";
const ERROR_CANON = "__error__";

function costText(costUsd: unknown): string {
  if (typeof costUsd !== "number" || !Number.isFinite(costUsd)) return "—";
  return `₹${(costUsd * 100).toFixed(2)}`;
}

function CompactValue({ value }: { value: unknown }): React.JSX.Element {
  if (value === null || value === undefined) {
    return <span className="italic text-[#8a8f98]">— not found</span>;
  }
  if (typeof value === "string" && value.trim() === "") {
    return <span className="italic text-[#8a8f98]">— not found</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="italic text-[#8a8f98]">— not found</span>;
    }
    return (
      <span className="flex min-w-0 flex-1 flex-wrap gap-1">
        {value.map((v, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <span
            key={i}
            title={formatValue(v)}
            className="max-w-full break-words rounded-full border border-[#e5e7eb] bg-[#f1f2f3] px-2 py-0.5 font-sans text-xs text-[#1d1d1d] dark:border-white/10 dark:bg-white/10 dark:text-[#F0EFEC]"
          >
            {formatValue(v)}
          </span>
        ))}
      </span>
    );
  }
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) {
      return <span className="italic text-[#8a8f98]">— not found</span>;
    }
    return (
      <span className="grid min-w-0 flex-1 gap-0.5">
        {entries.map(([k, v]) => (
          <span key={k} className="break-words font-mono text-xs" title={`${k}: ${formatValue(v)}`}>
            <span className="text-[#4a5058] dark:text-[#C3C2B7]">{k}: </span>
            <span className="text-[#1d1d1d] dark:text-[#F0EFEC]">{formatValue(v)}</span>
          </span>
        ))}
      </span>
    );
  }
  const text = formatValue(value);
  return (
    <span className="min-w-0 flex-1 break-words font-sans text-xs text-[#1d1d1d] dark:text-[#F0EFEC]" title={text}>
      {text}
    </span>
  );
}

/**
 * Narrow-screen comparison: horizontal strip of compact model summaries,
 * toolbar filters, then one card per attribute grouped by agent. Tapping a
 * value opens the AttributeCompareDrawer. No keyboard shortcuts.
 */
export function CompareCardList({
  columns,
  agents,
  editable,
  onRetry,
  showDoneBadge = true,
}: {
  columns: CompareColumn[];
  agents: CompareAgent[];
  editable: boolean;
  onRetry?: (key: string) => void;
  focusColumnKey?: string;
  showDoneBadge?: boolean;
}): React.JSX.Element {
  const model = useCompareModel(columns, agents);
  const {
    rowsByAgent,
    detail,
    setDetail,
    collapsed,
    toggleAgent,
    perCol,
    effectiveRating,
    hasRemarks,
    attrDescription,
    handleCellRate,
  } = model;

  const [drawerRow, setDrawerRow] = React.useState<CompareRow | null>(null);
  const drawerAgent = React.useMemo(() => {
    if (!drawerRow) return undefined;
    return agents.find((a) => a.id === drawerRow.agentId);
  }, [agents, drawerRow]);

  return (
    <div className="grid gap-3">
      <div
        className="flex gap-2 overflow-x-auto pb-1"
        role="list"
        aria-label="Model summaries"
      >
        {columns.map((col) => {
          const s = perCol.get(col.key) ?? { agreePct: 0, up: 0, down: 0, rated: 0, total: 0 };
          const usage = col.log?.usage;
          const showStatus =
            col.status === "running" ||
            col.status === "queued" ||
            col.status === "partial" ||
            col.status === "error" ||
            (col.status === "done" && showDoneBadge);
          return (
            <div
              key={col.key}
              role="listitem"
              className="min-w-36 shrink-0 rounded-2xl border border-[#e5e7eb] bg-white p-2.5 dark:border-white/10 dark:bg-[#1a1a1a]"
            >
              <p
                title={col.model}
                className="truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
              >
                {modelLabel(col.model, col.effort)}
              </p>
              {showStatus && (
                <p className="mt-0.5 font-sans text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                  {col.status === "running" || col.status === "queued" ? (
                    <span className="inline-flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                      {col.status}
                    </span>
                  ) : (
                    col.status
                  )}
                </p>
              )}
              <p className="mt-0.5 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                <span title={typeof usage?.cost_usd === "number" ? `$${usage.cost_usd} USD` : "Cost unknown"}>
                  {costText(usage?.cost_usd)}
                </span>
                {" · "}
                <span title="Wall time">{usage ? fmtMs(usage.duration_ms) : "—"}</span>
              </p>
              <p className="mt-0.5 font-sans text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                {s.agreePct.toFixed(0)}% agree · 👍{s.up} 👎{s.down}
              </p>
            </div>
          );
        })}
      </div>

      <CompareToolbar
        detail={detail}
        onDetail={setDetail}
      />

      {agents.map((agent) => {
        const agentRows = rowsByAgent.get(agent.id) ?? [];
        if (agentRows.length === 0) return null;
        const isCollapsed = collapsed.has(agent.id);
        return (
          <section key={agent.id} className="grid gap-2">
            <button
              type="button"
              onClick={() => toggleAgent(agent.id)}
              aria-expanded={!isCollapsed}
              className="flex w-full items-center gap-2 text-left font-heading text-xs font-bold text-[#1d1d1d] hover:text-[#0d5c4a] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#F0EFEC]"
            >
              <span aria-hidden="true">{isCollapsed ? "▸" : "▾"}</span>
              <span className="truncate">{agent.name} · {agentRows.length} attrs</span>
            </button>
            {!isCollapsed &&
              agentRows.map((row) => {
                const mark = agreementMark(row);
                const desc = attrDescription(row);
                return (
                  <article
                    key={`${row.agentId}|${row.attr}`}
                    className="rounded-2xl border border-[#e5e7eb] bg-white p-3 dark:border-white/10 dark:bg-[#1a1a1a]"
                  >
                    <header className="flex items-baseline gap-1.5">
                      <h4
                        className="min-w-0 flex-1 break-words font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                        title={desc || undefined}
                      >
                        {row.attr}
                      </h4>
                      <span aria-hidden="true" className="shrink-0 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                        {mark}
                      </span>
                    </header>
                    <ul className="mt-1.5 grid gap-1.5">
                      {columns.map((col) => {
                        const cell = row.cells[col.key];
                        if (!col.log) {
                          return (
                            <li key={col.key} className="flex items-center gap-2">
                              <span title={col.model} className="w-20 shrink-0 truncate font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                                {modelLabel(col.model, col.effort)}
                              </span>
                              {col.status === "error" ? (
                                <span className="flex-1 font-sans text-[11px] text-[#b91c1c] dark:text-[#f87171]">
                                  {col.error || "Column failed"}
                                  {onRetry && (
                                    <button
                                      type="button"
                                      onClick={() => onRetry(col.key)}
                                      className="ml-2 font-heading font-bold underline"
                                    >
                                      Retry
                                    </button>
                                  )}
                                </span>
                              ) : (
                                <span className="h-5 flex-1 animate-pulse rounded-lg bg-[#f1f2f3] dark:bg-white/10" aria-label="Loading…" />
                              )}
                            </li>
                          );
                        }
                        if (!cell || cell.canon === PENDING_CANON || cell.canon === ERROR_CANON || cell.error) {
                          return (
                            <li key={col.key} className="flex items-center gap-2">
                              <span title={col.model} className="w-20 shrink-0 truncate font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                                {modelLabel(col.model, col.effort)}
                              </span>
                              <span className="flex-1 font-sans text-[11px] text-[#b91c1c] dark:text-[#f87171]">
                                Agent failed{cell?.error ? ` · ${cell.error}` : ""}
                              </span>
                            </li>
                          );
                        }
                        const notFound = cell.canon === NOT_FOUND_CANON;
                        const rating = effectiveRating(row, col.key);
                        const confText =
                          typeof cell.confidence === "number" && Number.isFinite(cell.confidence)
                            ? cell.confidence.toFixed(2)
                            : "?";
                        const confTypeText =
                          typeof cell.confidenceType === "string" && cell.confidenceType.trim() !== ""
                            ? cell.confidenceType
                            : "?";
                        const evidenceText = typeof cell.evidence === "string" ? cell.evidence : "";
                        return (
                          <li key={col.key} className="flex items-start gap-2">
                            <span title={col.model} className="w-20 shrink-0 truncate pt-0.5 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                              {modelLabel(col.model, col.effort)}
                            </span>
                            <button
                              type="button"
                              onClick={() => setDrawerRow(row)}
                              title="Open attribute comparison"
                              className="grid min-w-0 flex-1 gap-0.5 rounded text-left focus-visible:outline-2 focus-visible:outline-brand"
                            >
                              {notFound ? (
                                <span className="italic font-sans text-xs text-[#8a8f98]">— not found</span>
                              ) : (
                                <CompactValue value={cell.value} />
                              )}
                              {detail !== "value" && !notFound && (
                                <span className="font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                                  conf {confText} · {confTypeText}
                                </span>
                              )}
                              {detail === "evidence" && !notFound && evidenceText.trim() !== "" && (
                                <span
                                  className="break-words font-sans text-[11px] italic text-[#4a5058] dark:text-[#C3C2B7]"
                                  title={evidenceText}
                                >
                                  “{evidenceText}”
                                </span>
                              )}
                            </button>
                            {hasRemarks(row, col.key) && (
                              <span
                                title="Has remarks"
                                aria-label="Has remarks"
                                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-[#1d1d1d] dark:bg-[#F0EFEC]"
                              />
                            )}
                            {editable && (
                              <span className="flex shrink-0 items-center">
                                <ThumbButtons value={rating} onChange={(n) => handleCellRate(row, col.key, n)} size="sm" />
                              </span>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </article>
                );
              })}
          </section>
        );
      })}

      <AttributeCompareDrawer
        row={drawerRow}
        columns={columns}
        agent={drawerAgent}
        open={drawerRow !== null}
        onClose={() => setDrawerRow(null)}
        editable={editable}
        model={model}
      />
    </div>
  );
}
