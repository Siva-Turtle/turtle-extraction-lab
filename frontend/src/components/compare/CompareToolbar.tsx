import { cn } from "../../lib/cn";

export type CompareFilter = "all" | "disagreements" | "unrated" | "rated-down";
export type CompareDetail = "value" | "confidence" | "evidence";

export type CompareCounts = {
  all: number;
  disagreements: number;
  unrated: number;
  ratedDown: number;
};

/**
 * Matrix toolbar: only the Detail switch remains (Value | +Confidence |
 * +Evidence) plus the "?" shortcuts button on desktop. Filter chips, merge /
 * link toggles, tally and agreement % were removed; merge is always OFF and
 * link is always ON.
 */
export function CompareToolbar({
  detail,
  onDetail,
  onShortcuts,
}: {
  detail: CompareDetail;
  onDetail: (d: CompareDetail) => void;
  filter?: CompareFilter;
  onFilter?: (f: CompareFilter) => void;
  merge?: boolean;
  onMerge?: (v: boolean) => void;
  link?: boolean;
  onLink?: (v: boolean) => void;
  counts?: CompareCounts;
  up?: number;
  down?: number;
  agreePct?: number;
  onShortcuts?: () => void;
}): React.JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <div
        className="flex items-center gap-1 rounded-full border border-[#e5e7eb] bg-[#f1f2f3] p-1 dark:border-white/10 dark:bg-white/5"
        role="group"
        aria-label="Detail mode"
      >
        {(
          [
            ["value", "Value"],
            ["confidence", "+Confidence"],
            ["evidence", "+Evidence"],
          ] as [CompareDetail, string][]
        ).map(([v, label]) => (
          <button
            key={v}
            type="button"
            onClick={() => onDetail(v)}
            aria-pressed={detail === v}
            className={cn(
              "rounded-full px-3 py-1 font-heading text-[11px] font-bold transition-colors",
              detail === v
                ? "bg-white text-[#1d1d1d] shadow-[0_1px_4px_rgba(29,29,29,0.12)] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
                : "text-[#4a5058] hover:text-[#1d1d1d] dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]",
            )}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-2 font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
        {onShortcuts && (
          <button
            type="button"
            onClick={onShortcuts}
            title="Keyboard shortcuts"
            aria-label="Keyboard shortcuts"
            className="flex h-7 w-7 items-center justify-center rounded-full border border-[#e5e7eb] font-mono text-xs font-bold text-[#4a5058] hover:border-[#1d1d1d] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:border-white/10 dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]"
          >
            ?
          </button>
        )}
      </div>
    </div>
  );
}
