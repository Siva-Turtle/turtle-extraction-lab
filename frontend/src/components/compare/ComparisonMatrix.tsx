import * as React from "react";
import { toast } from "sonner";
import { buildRows, columnStats, groupAgreementPct, linkedTargets, NOT_FOUND_CANON } from "../../lib/compare";
import type { CompareRow } from "../../lib/compare";
import type { CompareAgent, CompareColumn } from "../../lib/logTypes";
import { useFeedback } from "../../lib/useFeedback";
import type { FeedbackRateItem } from "../../lib/useFeedback";
import { cn } from "../../lib/cn";
import { Button } from "../ui/Button";
import { CompareCell, cellClassFor } from "./CompareCell";
import { CompareToolbar } from "./CompareToolbar";
import type { CompareDetail, CompareFilter } from "./CompareToolbar";
import { ModelColumnHeader } from "./ModelColumnHeader";

const PREFS_KEY = "lab:compare-prefs";
const PENDING_CANON = "__pending__";
const ERROR_CANON = "__error__";

type Prefs = { merge: boolean; link: boolean };

function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return { merge: true, link: true };
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return { merge: true, link: true };
    const rec = parsed as Record<string, unknown>;
    return {
      merge: rec.merge !== false,
      link: rec.link !== false,
    };
  } catch {
    return { merge: true, link: true };
  }
}

function snapshotAgentName(log: CompareColumn["log"], agentId: string, fallback: string): string {
  try {
    const snap = (log?.agent_snapshot ?? {})[agentId] as { name?: unknown } | undefined;
    if (snap && typeof snap.name === "string" && snap.name.trim() !== "") return snap.name;
  } catch {
    // fall through
  }
  return fallback;
}

function comparedKeys(row: CompareRow): string[] {
  const out: string[] = [];
  for (const keys of row.groups.values()) out.push(...keys);
  return out;
}

function agreementMark(row: CompareRow): string {
  if (row.state === "unanimous") return "✓";
  if (row.state === "majority") {
    let compared = 0;
    let largest = 0;
    for (const keys of row.groups.values()) {
      compared += keys.length;
      if (keys.length > largest) largest = keys.length;
    }
    return `≠ ${largest}/${compared}`;
  }
  if (row.state === "split") return "⚡";
  if (row.state === "none_found") return "∅";
  return "…";
}

/**
 * Multi-model comparison matrix with per-cell thumbs. Thumbs only on cells
 * (and merged cells) — never in column headers.
 */
export function ComparisonMatrix({
  columns,
  agents,
  editable,
  onRetry,
}: {
  columns: CompareColumn[];
  agents: CompareAgent[];
  editable: boolean;
  onRetry?: (key: string) => void;
}): React.JSX.Element {
  const feedback = useFeedback();
  const [prefs, setPrefs] = React.useState<Prefs>(loadPrefs);
  const [filter, setFilter] = React.useState<CompareFilter>("all");
  const [detail, setDetail] = React.useState<CompareDetail>("value");
  const [collapsed, setCollapsed] = React.useState<Set<string>>(new Set());

  React.useEffect(() => {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {
      // ignore persistence failures (private mode, quota)
    }
  }, [prefs]);

  const rows = React.useMemo(() => buildRows(agents, columns), [agents, columns]);

  function effectiveRating(row: CompareRow, colKey: string): "up" | "down" | null {
    const col = columns.find((c) => c.key === colKey);
    const log = col?.log;
    if (!log || !log.run_id) return null;
    const agentName = snapshotAgentName(log, row.agentId, row.agentName);
    return feedback.getRating(log.run_id, agentName, row.attr, row.cells[colKey]?.feedback ?? null);
  }

  function hasRemarks(row: CompareRow, colKey: string): boolean {
    const r = row.cells[colKey]?.feedback?.remarks;
    return typeof r === "string" && r.trim() !== "";
  }

  // Per-column stats: agreement % from compare.ts; up/down/rated layered
  // with the optimistic overlay so the header tallies match the thumbs.
  const perCol = React.useMemo(() => {
    const map = new Map<string, { agreePct: number; up: number; down: number; rated: number; total: number }>();
    for (const col of columns) {
      const base = columnStats(rows, col.key);
      let up = 0;
      let down = 0;
      for (const r of rows) {
        // eslint-disable-next-line react-hooks/purity
        const rating = effectiveRating(r, col.key);
        if (rating === "up") up += 1;
        else if (rating === "down") down += 1;
      }
      map.set(col.key, { agreePct: base.agreePct, up, down, rated: up + down, total: rows.length });
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, columns, feedback]);

  const overall = React.useMemo(() => {
    let up = 0;
    let down = 0;
    for (const col of columns) {
      up += perCol.get(col.key)?.up ?? 0;
      down += perCol.get(col.key)?.down ?? 0;
    }
    return { up, down, agreePct: groupAgreementPct(rows) };
  }, [columns, perCol, rows]);

  const counts = React.useMemo(() => {
    let disagreements = 0;
    let unrated = 0;
    let ratedDown = 0;
    for (const r of rows) {
      if (r.state === "majority" || r.state === "split") disagreements += 1;
      const keys = comparedKeys(r);
      if (keys.length === 0) continue;
      let anyRated = false;
      let anyDown = false;
      for (const k of keys) {
        // eslint-disable-next-line react-hooks/purity
        const rating = effectiveRating(r, k);
        if (rating === "up" || rating === "down") anyRated = true;
        if (rating === "down") anyDown = true;
      }
      if (!anyRated) unrated += 1;
      if (anyDown) ratedDown += 1;
    }
    return { all: rows.length, disagreements, unrated, ratedDown };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, columns, feedback]);

  const visibleRows = React.useMemo(() => {
    if (filter === "all") return rows;
    return rows.filter((r) => {
      if (filter === "disagreements") return r.state === "majority" || r.state === "split";
      const keys = comparedKeys(r);
      if (filter === "unrated") {
        if (keys.length === 0) return false;
        // eslint-disable-next-line react-hooks/purity
        return keys.every((k) => effectiveRating(r, k) === null);
      }
      if (filter === "rated-down") {
        // eslint-disable-next-line react-hooks/purity
        return keys.some((k) => effectiveRating(r, k) === "down");
      }
      return true;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, filter, columns, feedback]);

  // Cheapest / fastest among finished columns with numeric cost / time.
  const { cheapestKey, fastestKey } = React.useMemo(() => {
    const finished = columns.filter(
      (c) => c.log && (c.status === "done" || c.status === "partial"),
    );
    if (finished.length < 2) return { cheapestKey: "", fastestKey: "" };
    let cheapKey = "";
    let cheapCost = Number.POSITIVE_INFINITY;
    let fastKey = "";
    let fastMs = Number.POSITIVE_INFINITY;
    for (const c of finished) {
      const cost = c.log?.usage?.cost_usd;
      if (typeof cost === "number" && Number.isFinite(cost) && cost < cheapCost) {
        cheapCost = cost;
        cheapKey = c.key;
      }
      const ms = c.log?.usage?.duration_ms;
      if (typeof ms === "number" && Number.isFinite(ms) && ms < fastMs) {
        fastMs = ms;
        fastKey = c.key;
      }
    }
    return {
      cheapestKey: cheapCost === Number.POSITIVE_INFINITY ? "" : cheapKey,
      fastestKey: fastMs === Number.POSITIVE_INFINITY ? "" : fastKey,
    };
  }, [columns]);

  function buildItems(row: CompareRow, keys: string[], rating: "up" | "down" | ""): FeedbackRateItem[] {
    const items: FeedbackRateItem[] = [];
    for (const k of keys) {
      const col = columns.find((c) => c.key === k);
      const log = col?.log;
      if (!log || !log.run_id) continue;
      items.push({
        runId: log.run_id,
        agent: snapshotAgentName(log, row.agentId, row.agentName),
        attr: row.attr,
        rating,
      });
    }
    return items;
  }

  async function rateWithUndo(row: CompareRow, keys: string[], rating: "up" | "down" | ""): Promise<void> {
    const items = buildItems(row, keys, rating);
    if (items.length === 0) return;
    const prevItems: FeedbackRateItem[] = items.map((it) => {
      const col = columns.find((c) => c.log?.run_id === it.runId);
      const r = row;
      let prev: "up" | "down" | "" = "";
      if (col) {
        const cur = effectiveRating(r, col.key);
        prev = cur === "up" || cur === "down" ? cur : "";
      }
      return { ...it, rating: prev };
    });
    try {
      await feedback.rate(items);
    } catch {
      return;
    }
    if (items.length > 1) {
      const label =
        rating === "up"
          ? `Applied 👍 to ${items.length} models`
          : rating === "down"
            ? `Applied 👎 to ${items.length} models`
            : `Cleared ratings for ${items.length} models`;
      toast.success(label, {
        action: {
          label: "Undo",
          onClick: () => void feedback.rate(prevItems).catch(() => undefined),
        },
      });
    }
  }

  function handleCellRate(row: CompareRow, colKey: string, next: "up" | "down" | null): void {
    const rating: "up" | "down" | "" = next ?? "";
    const keys = prefs.link ? [colKey, ...linkedTargets(rows, row, colKey)] : [colKey];
    void rateWithUndo(row, keys, rating);
  }

  function handleMergedRate(row: CompareRow, next: "up" | "down" | null): void {
    const rating: "up" | "down" | "" = next ?? "";
    void rateWithUndo(
      row,
      columns.map((c) => c.key),
      rating,
    );
  }

  function toggleAgent(agentId: string): void {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      return next;
    });
  }

  const rowsByAgent = React.useMemo(() => {
    const map = new Map<string, CompareRow[]>();
    for (const a of agents) map.set(a.id, []);
    for (const r of visibleRows) {
      const list = map.get(r.agentId);
      if (list) list.push(r);
      else map.set(r.agentId, [r]);
    }
    return map;
  }, [agents, visibleRows]);

  const allFinished = columns.length > 0 && columns.every((c) => c.status === "done" || c.status === "partial");

  return (
    <div className="grid gap-3">
      <CompareToolbar
        filter={filter}
        onFilter={setFilter}
        merge={prefs.merge}
        onMerge={(v) => setPrefs((p) => ({ ...p, merge: v }))}
        link={prefs.link}
        onLink={(v) => setPrefs((p) => ({ ...p, link: v }))}
        detail={detail}
        onDetail={setDetail}
        counts={counts}
        up={overall.up}
        down={overall.down}
        agreePct={overall.agreePct}
      />
      <div className="max-h-[calc(100svh-140px)] overflow-auto rounded-2xl border border-[#e5e7eb] bg-white dark:border-white/10 dark:bg-[#1a1a1a]">
        <table className="w-full border-collapse text-left">
          <thead className="sticky top-0 z-20">
            <tr>
              <th
                scope="col"
                className="sticky left-0 z-30 min-w-[180px] border-b border-[#e5e7eb] bg-[#f1f2f3] p-3 font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#C3C2B7]"
              >
                Attribute
              </th>
              {columns.map((col) => {
                const s = perCol.get(col.key) ?? { agreePct: 0, up: 0, down: 0, rated: 0, total: rows.length };
                return (
                  <ModelColumnHeader
                    key={col.key}
                    column={col}
                    agreePct={s.agreePct}
                    up={s.up}
                    down={s.down}
                    rated={s.rated}
                    total={s.total}
                    isCheapest={col.key === cheapestKey}
                    isFastest={col.key === fastestKey}
                    onRetry={(k) => onRetry?.(k)}
                  />
                );
              })}
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => {
              const agentRows = rowsByAgent.get(agent.id) ?? [];
              const totalRows = rows.filter((r) => r.agentId === agent.id);
              const disagreements = totalRows.filter(
                (r) => r.state === "majority" || r.state === "split",
              ).length;
              if (agentRows.length === 0) return null;
              const isCollapsed = collapsed.has(agent.id);
              return (
                <React.Fragment key={agent.id}>
                  <tr>
                    <td
                      colSpan={1 + columns.length}
                      className="border-b border-[#e5e7eb] bg-[#f1f2f3]/70 p-0 dark:border-white/10 dark:bg-white/5"
                    >
                      <button
                        type="button"
                        onClick={() => toggleAgent(agent.id)}
                        aria-expanded={!isCollapsed}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left font-heading text-xs font-bold text-[#1d1d1d] hover:text-[#0d5c4a] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#F0EFEC]"
                      >
                        <span aria-hidden="true">{isCollapsed ? "▸" : "▾"}</span>
                        <span className="truncate">
                          {agent.name} · {totalRows.length} attrs · {disagreements} disagreements
                        </span>
                      </button>
                    </td>
                  </tr>
                  {!isCollapsed &&
                    agentRows.map((row) => {
                      const attrDesc =
                        agents
                          .find((a) => a.id === row.agentId)
                          ?.attributes.find((a) => a.name === row.attr)?.description ?? "";
                      const mark = agreementMark(row);
                      const merged =
                        prefs.merge && row.state === "unanimous" && allFinished && columns.length > 1;
                      if (merged) {
                        const firstKey = columns[0]?.key ?? "";
                        const firstCell = firstKey ? row.cells[firstKey] : undefined;
                        const ratings = columns.map((c) => effectiveRating(row, c.key));
                        const mergedRating =
                          ratings.length > 0 && ratings.every((r) => r === "up")
                            ? ("up" as const)
                            : ratings.length > 0 && ratings.every((r) => r === "down")
                              ? ("down" as const)
                              : null;
                        const anyRemarks = columns.some((c) => hasRemarks(row, c.key));
                        const tdClass = cellClassFor({
                          rated: mergedRating,
                          disagreesWithMajority: false,
                          isSplit: false,
                          notFound: false,
                        });
                        return (
                          <tr key={`${row.agentId}|${row.attr}`} className="border-b border-[#e5e7eb] last:border-0 dark:border-white/10">
                            <td className="sticky left-0 z-10 min-w-[180px] max-w-[240px] border-r border-[#e5e7eb] bg-white p-3 align-top dark:border-white/10 dark:bg-[#1a1a1a]">
                              <span className="flex items-start gap-1.5">
                                <span
                                  className="max-w-full flex-1 break-words font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                                  title={attrDesc || undefined}
                                >
                                  {row.attr}
                                </span>
                                <span
                                  aria-hidden="true"
                                  className="shrink-0 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]"
                                >
                                  {mark}
                                </span>
                              </span>
                            </td>
                            <td colSpan={columns.length} className={cn("min-w-[220px] p-3 align-top", tdClass)}>
                              {firstCell && (
                                <CompareCell
                                  value={firstCell.value}
                                  confidence={firstCell.confidence}
                                  confidenceType={firstCell.confidenceType}
                                  evidence={firstCell.evidence}
                                  detail={detail}
                                  rating={mergedRating}
                                  onRate={(n) => handleMergedRate(row, n)}
                                  editable={editable}
                                  hasRemarks={anyRemarks}
                                  notAccepted={false}
                                  notFound={false}
                                />
                              )}
                            </td>
                          </tr>
                        );
                      }
                      return (
                        <tr
                          key={`${row.agentId}|${row.attr}`}
                          className="border-b border-[#e5e7eb] last:border-0 dark:border-white/10"
                        >
                          <td className="sticky left-0 z-10 min-w-[180px] max-w-[240px] border-r border-[#e5e7eb] bg-white p-3 align-top dark:border-white/10 dark:bg-[#1a1a1a]">
                            <span className="flex items-start gap-1.5">
                              <span
                                className="max-w-full flex-1 break-words font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                                title={attrDesc || undefined}
                              >
                                {row.attr}
                              </span>
                              <span
                                aria-hidden="true"
                                className="shrink-0 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]"
                              >
                                {mark}
                              </span>
                            </span>
                          </td>
                          {columns.map((col) => {
                            const cell = row.cells[col.key];
                            // Running / queued (no log yet): skeleton shimmer.
                            if (!col.log || col.status === "running" || col.status === "queued") {
                              if (col.status === "error" && !col.log) {
                                return (
                                  <td
                                    key={col.key}
                                    className="min-w-[220px] border-l-4 border-l-[#ef4444] bg-[#fdecec] p-3 align-top dark:bg-[#ef4444]/10"
                                  >
                                    <p className="break-words font-sans text-xs text-[#b91c1c] dark:text-[#f87171]">
                                      {col.error || "Column failed"}
                                    </p>
                                    {onRetry && (
                                      <div className="mt-1.5">
                                        <Button variant="secondary" size="sm" onClick={() => onRetry(col.key)}>
                                          Retry
                                        </Button>
                                      </div>
                                    )}
                                  </td>
                                );
                              }
                              return (
                                <td key={col.key} className="min-w-[220px] p-3 align-top">
                                  <div
                                    aria-label="Loading…"
                                    className="h-8 animate-pulse rounded-lg bg-[#f1f2f3] dark:bg-white/10"
                                  />
                                </td>
                              );
                            }
                            // Agent-level failure for a finished column.
                            if (!cell || cell.canon === ERROR_CANON || cell.error) {
                              return (
                                <td
                                  key={col.key}
                                  className="min-w-[220px] border-l-4 border-l-[#ef4444] bg-[#fdecec] p-3 align-top dark:bg-[#ef4444]/10"
                                >
                                  <p className="break-words font-sans text-xs text-[#b91c1c] dark:text-[#f87171]">
                                    Agent failed{cell?.error ? ` · ${cell.error}` : ""}
                                  </p>
                                  {onRetry && (
                                    <div className="mt-1.5">
                                      <Button variant="secondary" size="sm" onClick={() => onRetry(col.key)}>
                                        Retry
                                      </Button>
                                    </div>
                                  )}
                                </td>
                              );
                            }
                            if (!cell || cell.canon === PENDING_CANON) {
                              return (
                                <td key={col.key} className="min-w-[220px] p-3 align-top">
                                  <div
                                    aria-label="Loading…"
                                    className="h-8 animate-pulse rounded-lg bg-[#f1f2f3] dark:bg-white/10"
                                  />
                                </td>
                              );
                            }
                            const rating = effectiveRating(row, col.key);
                            const notFound = cell.canon === NOT_FOUND_CANON;
                            const disagrees =
                              row.state === "majority" && cell.canon !== row.consensusKey;
                            const isSplit = row.state === "split";
                            // "≠ accepted": another cell in the row is rated
                            // up with a different canonical value.
                            let notAccepted = false;
                            for (const other of columns) {
                              if (other.key === col.key) continue;
                              const otherCell = row.cells[other.key];
                              if (!otherCell) continue;
                              if (otherCell.canon === PENDING_CANON || otherCell.canon === ERROR_CANON) continue;
                              if (otherCell.canon !== cell.canon && effectiveRating(row, other.key) === "up") {
                                notAccepted = true;
                                break;
                              }
                            }
                            const tdClass = cellClassFor({
                              rated: rating,
                              disagreesWithMajority: disagrees,
                              isSplit,
                              notFound,
                            });
                            return (
                              <td key={col.key} className={cn("min-w-[220px] p-3 align-top", tdClass)}>
                                <CompareCell
                                  value={cell.value}
                                  confidence={cell.confidence}
                                  confidenceType={cell.confidenceType}
                                  evidence={cell.evidence}
                                  detail={detail}
                                  rating={rating}
                                  onRate={(n) => handleCellRate(row, col.key, n)}
                                  editable={editable}
                                  hasRemarks={hasRemarks(row, col.key)}
                                  notAccepted={notAccepted}
                                  notFound={notFound}
                                />
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
