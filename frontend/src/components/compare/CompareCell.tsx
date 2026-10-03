import { formatValue } from "../../lib/format";
import { cn } from "../../lib/cn";
import { canonicalKey } from "../../lib/compare";
import type { ElementDiff } from "../../lib/compare";
import { ThumbButtons } from "../ui/ThumbButtons";
import type { CompareDetail } from "./CompareToolbar";

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function itemsDiffOf(diff: ElementDiff | undefined): { value: string; count: number }[] | null {
  if (!diff || !("items" in diff)) return null;
  return diff.items;
}

function totalOf(diff: ElementDiff | undefined): number | null {
  if (!diff || !("items" in diff)) return null;
  return diff.total;
}

function keysAgreeOf(diff: ElementDiff | undefined): Map<string, boolean> | null {
  if (!diff || !("keys" in diff)) return null;
  const m = new Map<string, boolean>();
  for (const k of diff.keys) m.set(k.key, k.agree);
  return m;
}

function ValueView({ value, diff }: { value: unknown; diff?: ElementDiff }): React.JSX.Element {
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
    const itemsDiff = itemsDiffOf(diff);
    const total = totalOf(diff);
    return (
      <span className="flex flex-wrap gap-1">
        {value.map((v, i) => {
          let count: number | null = null;
          if (itemsDiff !== null && total !== null) {
            const k = canonicalKey(v);
            const entry = itemsDiff.find((e) => canonicalKey(e.value) === k);
            count = entry ? entry.count : 0;
          }
          const disputed = count !== null && total !== null && count < total;
          return (
            <span
              // eslint-disable-next-line react/no-array-index-key
              key={i}
              title={formatValue(v)}
              className={cn(
                "max-w-full break-words rounded-full border px-2 py-0.5 font-sans text-[11px] text-[#1d1d1d] dark:text-[#F0EFEC]",
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
            </span>
          );
        })}
      </span>
    );
  }
  if (isPlainObject(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) {
      return <span className="italic text-[#8a8f98]">— not found</span>;
    }
    const agreeMap = keysAgreeOf(diff);
    return (
      <span className="grid gap-0.5">
        {entries.map(([k, v]) => {
          const agree = agreeMap?.get(k);
          const disputed = agree === false;
          return (
            <span
              key={k}
              className={cn(
                "break-words font-mono text-xs",
                disputed && "rounded bg-[#fef6e7] px-1 dark:bg-[#f59e0b]/10",
              )}
              title={`${k}: ${formatValue(v)}${disputed ? " (differs)" : ""}`}
            >
              <span className="text-[#4a5058] dark:text-[#C3C2B7]">{k}: </span>
              <span className="text-[#1d1d1d] dark:text-[#F0EFEC]">{formatValue(v)}</span>
            </span>
          );
        })}
      </span>
    );
  }
  const text = formatValue(value);
  return (
    <span className="break-words font-sans text-xs text-[#1d1d1d] dark:text-[#F0EFEC]" title={text}>
      {text}
    </span>
  );
}

/**
 * One matrix cell body: value first, optional confidence/evidence lines,
 * small thumbs right-aligned when editable, remarks dot + 💬 button,
 * "≠ accepted" mark. Clicking the value text opens the attribute drawer.
 */
export function CompareCell({
  value,
  confidence,
  confidenceType,
  evidence,
  detail,
  rating,
  onRate,
  editable,
  hasRemarks,
  notAccepted,
  notFound,
  onValueClick,
  onRemarksClick,
  remarksOpen,
  canRemark,
  diff,
}: {
  value: unknown;
  confidence?: unknown;
  confidenceType?: unknown;
  evidence?: unknown;
  detail: CompareDetail;
  rating: "up" | "down" | null;
  onRate: (next: "up" | "down" | null) => void;
  editable: boolean;
  hasRemarks: boolean;
  notAccepted: boolean;
  notFound: boolean;
  onValueClick?: () => void;
  onRemarksClick?: () => void;
  remarksOpen?: boolean;
  canRemark?: boolean;
  diff?: ElementDiff;
}): React.JSX.Element {
  const confText =
    typeof confidence === "number" && Number.isFinite(confidence) ? confidence.toFixed(2) : "?";
  const confTypeText =
    typeof confidenceType === "string" && confidenceType.trim() !== "" ? confidenceType : "?";
  const evidenceText = typeof evidence === "string" ? evidence : "";
  const remarkAllowed = canRemark ?? rating !== null;
  const showRemarksButton = editable && typeof onRemarksClick === "function";

  const valueNode = notFound ? (
    <span className="italic text-[#8a8f98]">— not found</span>
  ) : (
    <ValueView value={value} diff={diff} />
  );

  return (
    <div className="group/cell flex min-w-0 flex-col gap-1">
      {onValueClick && !notFound ? (
        <button
          type="button"
          onClick={onValueClick}
          title="Open attribute comparison"
          className="min-w-0 cursor-pointer rounded text-left focus-visible:outline-2 focus-visible:outline-brand"
        >
          {valueNode}
        </button>
      ) : (
        valueNode
      )}
      {detail !== "value" && !notFound && (
        <span className="font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
          conf {confText} · {confTypeText}
        </span>
      )}
      {detail === "evidence" && !notFound && evidenceText.trim() !== "" && (
        <span
          className="break-words italic font-sans text-[11px] text-[#4a5058] dark:text-[#C3C2B7]"
          title={evidenceText}
        >
          “{evidenceText}”
        </span>
      )}
      <div className="flex items-center justify-end gap-1.5">
        {notAccepted && (
          <span
            title="Another model with a different value is rated up"
            className="font-sans text-[10px] font-bold text-[#b45309] dark:text-[#fbbf24]"
          >
            ≠ accepted
          </span>
        )}
        {hasRemarks && (
          <span
            title="Has remarks"
            aria-label="Has remarks"
            className="h-1.5 w-1.5 rounded-full bg-[#1d1d1d] dark:bg-[#F0EFEC]"
          />
        )}
        {editable && (
          <span className="flex items-center gap-1">
            <ThumbButtons value={rating} onChange={onRate} size="sm" />
            {showRemarksButton && (
              <button
                type="button"
                onClick={onRemarksClick}
                disabled={!remarkAllowed}
                title={remarkAllowed ? "Edit remarks" : "Rate first"}
                aria-label="Edit remarks"
                aria-pressed={remarksOpen}
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full border border-[#e5e7eb] text-xs text-[#4a5058] transition-opacity hover:border-[#1d1d1d] disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-[#C3C2B7]",
                  hasRemarks || remarksOpen
                    ? "opacity-100"
                    : "opacity-0 focus-visible:opacity-100 group-hover/cell:opacity-100 group-focus-within/cell:opacity-100",
                )}
              >
                💬
              </button>
            )}
          </span>
        )}
      </div>
    </div>
  );
}

export function cellClassFor(params: {
  rated: "up" | "down" | null;
  disagreesWithMajority: boolean;
  isSplit: boolean;
  notFound: boolean;
}): string {
  if (params.rated === "down") return "border-l-4 border-l-[#ef4444] bg-[#fdecec] dark:bg-[#ef4444]/10";
  if (params.rated === "up")
    return "border-l-4 border-l-[#22c55e] bg-[#e9f9ef]/60 dark:bg-[#22c55e]/10";
  if (params.disagreesWithMajority)
    return "border-l-4 border-l-[#f59e0b] bg-[#fef6e7] dark:bg-[#f59e0b]/10";
  if (params.isSplit) return "bg-[#eaf1fe] dark:bg-[#3b82f6]/10";
  if (params.notFound) return cn();
  return cn();
}
