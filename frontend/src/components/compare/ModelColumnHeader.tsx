import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { fmtCost, fmtMs, fmtTokens, modelLabel, USD_TO_INR } from "../../lib/format";
import type { CompareColumn } from "../../lib/logTypes";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";
import { ReusedBadge, isFullyReused, reusedCounts } from "./ReusedBadge";

function Elapsed({ startedAt }: { startedAt?: number }): React.JSX.Element | null {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  if (!startedAt) return null;
  return <span>{Math.max(0, Math.round((now - startedAt) / 1000))}s</span>;
}

function costDisplay(costUsd: unknown): { text: string; title: string } {
  if (typeof costUsd !== "number" || !Number.isFinite(costUsd)) {
    return { text: "—", title: "Cost unknown" };
  }
  return {
    text: `₹${(costUsd * USD_TO_INR).toFixed(2)}`,
    title: `${fmtCost(costUsd)} USD`,
  };
}

/**
 * Sticky top header cell for one model column: name, effort, status, cost,
 * timing, agreement %, tallies, cheapest/fastest badges, ⋯ menu.
 * NO thumbs in the header.
 */
export function ModelColumnHeader({
  column,
  agreePct,
  up,
  down,
  rated,
  total,
  isCheapest,
  isFastest,
  onRetry,
  showDoneBadge = true,
}: {
  column: CompareColumn;
  agreePct: number;
  up: number;
  down: number;
  rated: number;
  total: number;
  isCheapest: boolean;
  isFastest: boolean;
  onRetry: (key: string) => void;
  showDoneBadge?: boolean;
}): React.JSX.Element {
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!menuOpen) return;
    function onDoc(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

  const usage = column.log?.usage;
  const cost = costDisplay(usage?.cost_usd);
  const consistencyScore = column.log?.consistency?.score;
  const consistencyText =
    typeof consistencyScore === "number" && Number.isFinite(consistencyScore)
      ? `Consistency ${Math.round(consistencyScore * 100)}%`
      : null;
  const wall = usage ? fmtMs(usage.duration_ms) : "—";
  const tokens =
    usage && typeof usage.prompt_tokens === "number" && typeof usage.completion_tokens === "number"
      ? `${fmtTokens(usage.prompt_tokens)}→${fmtTokens(usage.completion_tokens)}`
      : "—";
  const reuse = column.log ? reusedCounts(column.log) : { reused: 0, total: 0 };
  const fullyReused = column.log ? isFullyReused(column.log) : false;
  const partiallyReused = reuse.reused > 0 && reuse.total > 0 && reuse.reused < reuse.total;

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(column.log?.outputs ?? {}, null, 2));
      toast.success("Outputs copied");
    } catch {
      toast.error("Copy failed");
    }
    setMenuOpen(false);
  }

  return (
    <th
      scope="col"
      className="break-words border-b border-[#e5e7eb] bg-white p-3 text-left align-top [overflow-wrap:anywhere] dark:border-white/10 dark:bg-[#1a1a1a]"
    >
      <div className="grid min-w-0 gap-1.5">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span
            title={`${column.model}${column.provider ? ` · ${column.provider}` : " · Auto"}`}
            className="min-w-0 flex-1 break-words font-mono text-xs font-bold text-[#1d1d1d] [overflow-wrap:anywhere] dark:text-[#F0EFEC]"
          >
            {modelLabel(column.model, column.effort, column.provider ?? column.log?.provider ?? "")}
          </span>
          {column.log && fullyReused && <ReusedBadge log={column.log} />}
          {column.log && partiallyReused && (
            <span
              className="font-sans text-[11px] text-[#4a5058] dark:text-[#C3C2B7]"
              title="Some agents reused previous output; the rest ran fresh"
            >
              {reuse.reused} of {reuse.total} agents reused
            </span>
          )}
          <div className="relative shrink-0" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((o) => !o)}
              aria-label={`Column actions for ${column.model}`}
              aria-expanded={menuOpen}
              className="flex h-7 w-7 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#f1f2f3] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]"
            >
              ⋯
            </button>
            {menuOpen && (
              <div className="absolute right-0 top-full z-30 mt-1 min-w-36 rounded-xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] dark:border-white/10 dark:bg-[#1a1a1a]">
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    onRetry(column.key);
                  }}
                  className="block w-full rounded-lg px-3 py-2 text-left font-sans text-sm text-[#1d1d1d] hover:bg-[#e8fbf6] dark:text-[#F0EFEC] dark:hover:bg-white/10"
                >
                  Retry
                </button>
                <button
                  type="button"
                  onClick={() => void copyJson()}
                  disabled={!column.log}
                  className="block w-full rounded-lg px-3 py-2 text-left font-sans text-sm text-[#1d1d1d] hover:bg-[#e8fbf6] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#F0EFEC] dark:hover:bg-white/10"
                >
                  Copy JSON
                </button>
              </div>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {showDoneBadge && column.status === "done" && <Badge tone="success">done</Badge>}
          {column.status === "partial" && <Badge tone="warning">partial</Badge>}
          {column.status === "error" && <Badge tone="danger">error</Badge>}
          {(column.status === "running" || column.status === "queued") && (
            <Badge tone="info">
              <span className="inline-flex items-center gap-1">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                {column.status === "running" ? <Elapsed startedAt={column.startedAt} /> : column.status}
              </span>
            </Badge>
          )}
          {isCheapest && <Badge tone="success">cheapest</Badge>}
          {isFastest && <Badge tone="info">fastest</Badge>}
        </div>
        <div
          className={cn(
            "break-words font-mono text-[11px] text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]",
            column.status === "error" && !column.log && "text-[#b91c1c] dark:text-[#f87171]",
          )}
          title={column.status === "error" && !column.log ? column.error : undefined}
        >
          {column.status === "error" && !column.log ? (
            <span className="break-words font-sans text-xs [overflow-wrap:anywhere]">{column.error || "Column failed"}</span>
          ) : (
            <>
              <span title={cost.title}>{cost.text}</span>
              {" · "}
              <span title="Wall time">{wall}</span>
              {" · "}
              <span title="Tokens in → out">{tokens}</span>
            </>
          )}
        </div>
        <div className="break-words font-sans text-[11px] text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]">
          <span title="Average similarity to the other models">agrees {agreePct.toFixed(0)}%</span>
          {" · "}
          <span title="Thumbs up / down on this column">
            👍{up} 👎{down}
          </span>
          {" · "}
          <span title="Rated rows out of total rows">
            rated {rated}/{total}
          </span>
          {consistencyText && (
            <>
              {" · "}
              <span title="Identifier's predicted attributes vs what the agents actually extracted">
                {consistencyText}
              </span>
            </>
          )}
        </div>
      </div>
    </th>
  );
}
