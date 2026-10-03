import * as React from "react";
import { formatValue, modelLabel } from "../../lib/format";
import { canonicalKey } from "../../lib/compare";
import type { CompareRow } from "../../lib/compare";
import type { CompareAgent, CompareColumn } from "../../lib/logTypes";
import { Drawer } from "../ui/Drawer";
import { ThumbButtons } from "../ui/ThumbButtons";
import { RemarksPopover } from "../ui/RemarksPopover";
import type { CompareModel } from "./useCompareModel";

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function consensusObject(row: CompareRow, columns: CompareColumn[]): Record<string, unknown> | null {
  if (!row.consensusKey) return null;
  for (const col of columns) {
    const cell = row.cells[col.key];
    if (cell && cell.canon === row.consensusKey && isPlainObject(cell.value)) {
      return cell.value;
    }
  }
  return null;
}

function FullValue({
  value,
  consensus,
}: {
  value: unknown;
  consensus: Record<string, unknown> | null;
}): React.JSX.Element {
  if (value === null || value === undefined || (typeof value === "string" && value.trim() === "")) {
    return <span className="italic text-[#8a8f98]">— not found</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="italic text-[#8a8f98]">— not found</span>;
    return (
      <ul className="grid gap-1">
        {value.map((v, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <li
            key={i}
            className="break-words rounded-lg border border-[#e5e7eb] bg-[#f1f2f3] px-2 py-1 font-sans text-xs text-[#1d1d1d] dark:border-white/10 dark:bg-white/10 dark:text-[#F0EFEC]"
          >
            {formatValue(v)}
          </li>
        ))}
      </ul>
    );
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return <span className="italic text-[#8a8f98]">— not found</span>;
    return (
      <table className="w-full font-mono text-xs">
        <tbody>
          {entries.map(([k, v]) => {
            const differs =
              consensus !== null &&
              k in consensus &&
              canonicalKey(v) !== canonicalKey((consensus as Record<string, unknown>)[k]);
            return (
              <tr
                key={k}
                className={
                  differs
                    ? "bg-[#fef6e7] dark:bg-[#f59e0b]/10"
                    : undefined
                }
                title={differs ? "Differs from consensus" : undefined}
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
  const consensus = consensusObject(row, columns);

  return (
    <Drawer
      closeLabel="Close attribute comparison"
      onClose={onClose}
      title={
        <div className="grid gap-1">
          <p className="break-words font-heading text-sm font-bold text-[#1d1d1d] dark:text-[#F0EFEC]">
            {row.attr}
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
          const rating = model.effectiveRating(row, col.key);
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
                <FullValue value={cell.value} consensus={consensus} />
              </div>
              <p className="mt-1.5 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                conf {confText} · {confTypeText}
              </p>
              {evidenceText.trim() !== "" && (
                <p
                  className="mt-1 break-words font-sans text-[11px] italic text-[#4a5058] dark:text-[#C3C2B7]"
                  title={evidenceText}
                >
                  “{evidenceText}”
                </p>
              )}
              <div className="relative mt-2 flex items-center justify-end gap-1.5">
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
