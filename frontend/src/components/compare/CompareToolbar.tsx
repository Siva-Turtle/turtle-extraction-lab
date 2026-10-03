import { cn } from "../../lib/cn";

export type CompareFilter = "all" | "disagreements" | "unrated" | "rated-down";
export type CompareDetail = "value" | "confidence" | "evidence";

export type CompareCounts = {
  all: number;
  disagreements: number;
  unrated: number;
  ratedDown: number;
};

function Chip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-2.5 py-1 font-heading text-[11px] font-bold uppercase tracking-wide transition-colors",
        active
          ? "border-[#1d1d1d] bg-[#1d1d1d] text-white dark:border-[#2fdebf] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
          : "border-[#e5e7eb] bg-white text-[#4a5058] hover:border-[#1d1d1d] hover:text-[#1d1d1d] dark:border-white/10 dark:bg-transparent dark:text-[#C3C2B7] dark:hover:border-white/40 dark:hover:text-[#F0EFEC]",
      )}
    >
      {label}
    </button>
  );
}

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
}): React.JSX.Element {
  return (
    <label className="inline-flex cursor-pointer items-center gap-1.5 font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/**
 * Matrix toolbar: filter chips with counts, merge/link toggles, detail
 * switch, overall tally + agreement %.
 */
export function CompareToolbar({
  filter,
  onFilter,
  merge,
  onMerge,
  link,
  onLink,
  detail,
  onDetail,
  counts,
  up,
  down,
  agreePct,
  onShortcuts,
}: {
  filter: CompareFilter;
  onFilter: (f: CompareFilter) => void;
  merge: boolean;
  onMerge: (v: boolean) => void;
  link: boolean;
  onLink: (v: boolean) => void;
  detail: CompareDetail;
  onDetail: (d: CompareDetail) => void;
  counts: CompareCounts;
  up: number;
  down: number;
  agreePct: number;
  onShortcuts?: () => void;
}): React.JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Row filter">
        <Chip active={filter === "all"} onClick={() => onFilter("all")} label={`All ${counts.all}`} />
        <Chip
          active={filter === "disagreements"}
          onClick={() => onFilter("disagreements")}
          label={`Disagreements ${counts.disagreements}`}
        />
        <Chip
          active={filter === "unrated"}
          onClick={() => onFilter("unrated")}
          label={`Unrated ${counts.unrated}`}
        />
        <Chip
          active={filter === "rated-down"}
          onClick={() => onFilter("rated-down")}
          label={`Rated 👎 ${counts.ratedDown}`}
        />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Toggle checked={merge} onChange={onMerge} label="Merge identical" />
        <Toggle checked={link} onChange={onLink} label="Link identical" />
      </div>
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
        <span title="Thumbs up total">
          👍{up}
        </span>
        <span title="Thumbs down total">
          👎{down}
        </span>
        <span title="Mean agreement across compared rows">{agreePct.toFixed(0)}% agree</span>
      </div>
    </div>
  );
}
