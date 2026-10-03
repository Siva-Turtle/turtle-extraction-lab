import * as React from "react";
import { formatValue, modelLabel } from "../../lib/format";
import { cn } from "../../lib/cn";
import { canonicalKey } from "../../lib/compare";
import type { CompareRow, ElementDiff } from "../../lib/compare";
import type { CompareAgent, CompareColumn } from "../../lib/logTypes";
import { Badge } from "../ui/Badge";
import { Drawer } from "../ui/Drawer";
import { ThumbButtons } from "../ui/ThumbButtons";
import { RemarksPopover } from "../ui/RemarksPopover";
import type { CompareModel } from "./useCompareModel";
import { UnwrappedListTable } from "./UnwrappedListTable";

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function agreementTone(score: number | null): "success" | "warning" | "danger" {
  if (score === null) return "warning";
  const pct = score * 100;
  if (pct >= 90) return "success";
  if (pct >= 50) return "warning";
  return "danger";
}

function FullValue({
  value,
  diff,
  unwrapped,
}: {
  value: unknown;
  diff?: ElementDiff;
  unwrapped?: boolean;
}): React.JSX.Element {
  if (value === null || value === undefined || (typeof value === "string" && value.trim() === "")) {
    return <span className="italic text-[#8a8f98]">— not found</span>;
  }
  if (unwrapped && Array.isArray(value)) {
    return <UnwrappedListTable value={value} />;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="italic text-[#8a8f98]">— not found</span>;
    const hasItems = !!diff && "items" in diff;
    const total = hasItems ? (diff as { total: number }).total : null;
    const items = hasItems ? (diff as { items: { value: string; count: number }[] }).items : null;
    return (
      <ul className="flex flex-wrap gap-1">
        {value.map((v, i) => {
          let count: number | null = null;
          if (items !== null && total !== null) {
            const k = canonicalKey(v);
            const entry = items.find((e) => canonicalKey(e.value) === k);
            count = entry ? entry.count : 0;
          }
          const disputed = count !== null && total !== null && count < total;
          return (
            // eslint-disable-next-line react/no-array-index-key
            <li
              key={i}
              className={cn(
                "break-words rounded-full border px-2 py-1 font-sans text-xs text-[#1d1d1d] dark:text-[#F0EFEC]",
                disputed
                  ? "border-[#f59e0b] bg-[#fef6e7] dark:border-[#f59e0b]/40 dark:bg-[#f59e0b]/15"
                  : "border-[#e5e7eb] bg-[#f1f2f3] dark:border-white/10 dark:bg-white/10",
              )}
            >
              {formatValue(v)}
              {disputed && total !== null && count !== null && (
                <sup className="ml-1 font-mono text-[10px] text-[#b45309] dark:text-[#fbbf24]">
                  {count}/{total}
                </sup>
              )}
            </li>
          );
        })}
      </ul>
    );
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <span className="italic text-[#8a8f98]">— not found</span>;
    const agreeMap =
      diff && "keys" in diff
        ? new Map((diff as { keys: { key: string; agree: boolean }[] }).keys.map((k) => [k.key, k.agree]))
        : null;
    return (
      <table className="w-full font-mono text-xs">
        <tbody>
          {entries.map(([k, v]) => {
            const disputed = agreeMap?.get(k) === false;
            return (
              <tr
                key={k}
                className={disputed ? "bg-[#fef6e7] dark:bg-[#f59e0b]/10" : undefined}
                title={disputed ? "Differs across models" : undefined}
              >
                <td className="break-words px-2 py-1 align-top text-[#4a5058] dark:text-[#C3C2B7]">
                  {k}
                </td>
                <td className="break-words px-2 py-1 align-top text-[#1d1d1d] dark:text-[#F0EFEC]">
                  {formatValue(v)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }
  return (
    <span className="break-words font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]">
      {formatValue(value)}
    </span>
  );
}

/**
 * Full attribute comparison drawer: description + type, then one block per
 * model column with the full value, confidence, evidence, thumbs (same
 * linked rate logic as the matrix) and the remarks field.
 */
export function AttributeCompareDrawer({
  row,
  columns,
  agent,
  open,
  onClose,
  editable,
  model,
}: {
  row: CompareRow | null;
  columns: CompareColumn[];
  agent: CompareAgent | undefined;
  open: boolean;
  onClose: () => void;
  editable: boolean;
  model: CompareModel;
}): React.JSX.Element | null {
  const [remarksFor, setRemarksFor] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!open) setRemarksFor(null);
  }, [open, row]);

  if (!open || !row) return null;

  const attrMeta = agent?.attributes.find((a) => a.name === row.attr);

  return (
    <Drawer
      closeLabel="Close attribute comparison"
      onClose={onClose}
      title={
        <div className="grid gap-1">
          <p className="flex flex-wrap items-center gap-2 break-words font-heading text-sm font-bold text-[#1d1d1d] dark:text-[#F0EFEC]">
            <span>{row.attr}</span>
            {row.allEmpty ? (
              <span className="font-sans text-[11px] font-normal italic text-[#8a8f98]">
                — empty
              </span>
            ) : row.score !== null ? (
              <Badge tone={agreementTone(row.score)}>{Math.round((row.score as number) * 100)}%</Badge>
            ) : null}
          </p>
          <p className="font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
            {agent?.name ?? row.agentName}
            {attrMeta?.type ? ` · ${attrMeta.type}` : ""}
          </p>
          {attrMeta?.description ? (
            <p className="break-words font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
              {attrMeta.description}
            </p>
          ) : null}
        </div>
      }
    >
      <div className="grid gap-3">
        {columns.map((col) => {
          const cell = row.cells[col.key];
          if (!cell || !col.log) return null;
          if (cell.canon === "__not_selected__") {
            return (
              <section
                key={col.key}
                className="rounded-2xl border border-[#e5e7eb] p-3 dark:border-white/10"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span
                    title={col.model}
                    className="max-w-48 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                  >
                    {modelLabel(col.model, col.effort)}
                  </span>
                </div>
                <p className="mt-2 font-sans text-xs italic text-[#8a8f98]">not selected</p>
              </section>
            );
          }
          const rating = model.effectiveRating(row, col.key);
          const auto = model.isAutoFeedback(row, col.key);
          const remarks = model.getRemarksText(row, col.key);
          const canRemark = rating !== null;
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
            <section
              key={col.key}
              className="rounded-2xl border border-[#e5e7eb] p-3 dark:border-white/10"
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <span
                  title={col.model}
                  className="max-w-48 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                >
                  {modelLabel(col.model, col.effort)}
                </span>
              </div>
              <div className="mt-2">
                <FullValue value={cell.value} diff={row.elementDiff} unwrapped={cell.unwrapped} />
              </div>
              {!cell.unwrapped && (
                <p className="mt-1.5 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                  conf {confText} · {confTypeText}
                </p>
              )}
              {!cell.unwrapped && evidenceText.trim() !== "" && (
                <p
                  className="mt-1 break-words font-sans text-[11px] italic text-[#4a5058] dark:text-[#C3C2B7]"
                  title={evidenceText}
                >
                  “{evidenceText}”
                </p>
              )}
              <div className="relative mt-2 flex items-center justify-end gap-1.5">
                {auto && (
                  <span
                    title="Automatically flagged: identifier listed it but the agent returned nothing, or vice versa"
                    className="rounded-full border border-[#e5e7eb] px-1.5 py-0.5 font-sans text-[10px] font-bold text-[#4a5058] dark:border-white/10 dark:text-[#C3C2B7]"
                  >
                    Auto
                  </span>
                )}
                {remarks.trim() !== "" && (
                  <span
                    className="min-w-0 max-w-48 flex-1 truncate text-left font-sans text-[11px] text-[#4a5058] dark:text-[#C3C2B7]"
                    title={remarks}
                  >
                    💬 {remarks}
                  </span>
                )}
                {editable && (
                  <>
                    <ThumbButtons
                      value={rating}
                      onChange={(n) => model.handleCellRate(row, col.key, n)}
                      size="sm"
                    />
                    <button
                      type="button"
                      onClick={() => setRemarksFor((cur) => (cur === col.key ? null : col.key))}
                      disabled={!canRemark}
                      title={canRemark ? "Edit remarks" : "Rate first"}
                      aria-label={`Remarks for ${modelLabel(col.model, col.effort)} / ${row.attr}`}
                      aria-pressed={remarksFor === col.key}
                      className="flex h-7 w-7 items-center justify-center rounded-full border border-[#e5e7eb] text-xs text-[#4a5058] transition-colors hover:border-[#1d1d1d] disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-[#C3C2B7]"
                    >
                      💬
                    </button>
                  </>
                )}
                {remarksFor === col.key && (
                  <RemarksPopover
                    value={remarks}
                    onClose={() => setRemarksFor(null)}
                    onSave={(text) => {
                      void model
                        .saveRemarksFor(row, col.key, text)
                        .then(() => setRemarksFor(null))
                        .catch(() => undefined);
                    }}
                  />
                )}
              </div>
            </section>
          );
        })}
      </div>
    </Drawer>
  );
}
