import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { fmtCost, fmtMs, fmtTokens, shortModel, USD_TO_INR } from "../../lib/format";
import type { CompareColumn } from "../../lib/logTypes";
import { cn } from "../../lib/cn";
import { Badge } from "../ui/Badge";

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
  const wall = usage ? fmtMs(usage.duration_ms) : "—";
  const tokens =
    usage && typeof usage.prompt_tokens === "number" && typeof usage.completion_tokens === "number"
      ? `${fmtTokens(usage.prompt_tokens)}→${fmtTokens(usage.completion_tokens)}`
      : "—";

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
      className="min-w-[220px] max-w-[320px] border-b border-[#e5e7eb] bg-white p-3 text-left align-top dark:border-white/10 dark:bg-[#1a1a1a]"
    >
      <div className="grid gap-1.5">
        <div className="flex min-w-0 items-center gap-1.5">
          <span
            title={column.model}
            className="min-w-0 flex-1 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
          >
            {shortModel(column.model)}
          </span>
          <div className="relative" ref={menuRef}>
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
          <Badge tone="neutral">{column.effort || "default"}</Badge>
          {column.status === "done" && <Badge tone="success">done</Badge>}
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
            "font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]",
            column.status === "error" && !column.log && "text-[#b91c1c] dark:text-[#f87171]",
          )}
          title={column.status === "error" && !column.log ? column.error : undefined}
        >
          {column.status === "error" && !column.log ? (
            <span className="break-words font-sans text-xs">{column.error || "Column failed"}</span>
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
        <div className="font-sans text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
          <span title="Share of compared rows sitting in the row consensus">agrees {agreePct.toFixed(0)}%</span>
          {" · "}
          <span title="Thumbs up / down on this column">
            👍{up} 👎{down}
          </span>
          {" · "}
          <span title="Rated rows out of total rows">
            rated {rated}/{total}
          </span>
        </div>
      </div>
    </th>
  );
}
