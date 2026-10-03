import * as React from "react";
import { cn } from "../../lib/cn";
import { NOT_FOUND_CANON } from "../../lib/compare";
import type { CompareRow } from "../../lib/compare";
import type { CompareAgent, CompareColumn } from "../../lib/logTypes";
import { useIsNarrow } from "../../lib/useIsNarrow";
import { Button } from "../ui/Button";
import { Modal } from "../ui/Modal";
import { RemarksPopover } from "../ui/RemarksPopover";
import { CompareCell, cellClassFor } from "./CompareCell";
import { CompareToolbar } from "./CompareToolbar";
import { ModelColumnHeader } from "./ModelColumnHeader";
import { AttributeCompareDrawer } from "./AttributeCompareDrawer";
import { CompareCardList } from "./CompareCardList";
import { agreementMark, useCompareModel } from "./useCompareModel";

const PENDING_CANON = "__pending__";
const ERROR_CANON = "__error__";

type FocusCell = { row: CompareRow; colKey: string; merged: boolean; key: string };

function cellKeyFor(row: CompareRow, colKey: string, merged: boolean): string {
  return merged ? `merged:${row.agentId}|${row.attr}` : `cell:${row.agentId}|${row.attr}|${colKey}`;
}

const SHORTCUTS: [string, string][] = [
  ["Arrow keys", "Move between cells"],
  ["U / D", "Rate focused cell up / down, then move down one row"],
  ["X or 0", "Clear rating on focused cell"],
  ["R", "Open remarks for focused cell (needs a rating)"],
  ["Enter / Space", "Open attribute comparison drawer"],
  ["N", "Jump to next unrated cell (disagreements first)"],
  ["?", "Open this shortcuts sheet"],
];

/**
 * Multi-model comparison matrix with per-cell thumbs. Thumbs only on cells
 * (and merged cells) — never in column headers. Keyboard: grid with roving
 * tabindex; narrow screens render CompareCardList instead.
 */
export function ComparisonMatrix({
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
  const narrow = useIsNarrow();
  const model = useCompareModel(columns, agents);
  const {
    visibleRows,
    rowsByAgent,
    detail,
    setDetail,
    collapsed,
    toggleAgent,
    perCol,
    cheapestKey,
    fastestKey,
    effectiveRating,
    hasRemarks,
    getRemarksText,
    attrDescription,
    handleCellRate,
    saveRemarksFor,
  } = model;

  const [drawerRow, setDrawerRow] = React.useState<CompareRow | null>(null);
  const [remarksFor, setRemarksFor] = React.useState<string | null>(null);
  const [shortcutsOpen, setShortcutsOpen] = React.useState(false);
  const [focusKey, setFocusKey] = React.useState<string | null>(null);
  const [gridActive, setGridActive] = React.useState(false);
  const gridRef = React.useRef<HTMLTableElement>(null);

  // Flat grid order (render order): agent sections in `agents` order.
  // Merge identical is always OFF: every model gets its own cell.
  const gridRows = React.useMemo((): { row: CompareRow; cells: FocusCell[] }[] => {
    const out: { row: CompareRow; cells: FocusCell[] }[] = [];
    for (const agent of agents) {
      const list = rowsByAgent.get(agent.id) ?? [];
      if (list.length === 0) continue;
      for (const row of list) {
        out.push({
          row,
          cells: columns.map((c) => ({ row, colKey: c.key, merged: false, key: cellKeyFor(row, c.key, false) })),
        });
      }
    }
    return out;
  }, [agents, rowsByAgent, columns]);

  const firstKey = gridRows[0]?.cells[0]?.key ?? null;
  const activeKey = focusKey ?? firstKey;

  const drawerAgent = React.useMemo(() => {
    if (!drawerRow) return undefined;
    return agents.find((a) => a.id === drawerRow.agentId);
  }, [agents, drawerRow]);

  function focusCell(key: string): void {
    setFocusKey(key);
    requestAnimationFrame(() => {
      try {
        const el = gridRef.current?.querySelector<HTMLElement>(
          `[data-cell-key="${typeof CSS !== "undefined" && CSS.escape ? CSS.escape(key) : key}"]`,
        );
        el?.focus({ preventScroll: false });
      } catch {
        // ignore focus failures (unmounted during fast navigation)
      }
    });
  }

  function locate(key: string | null): { ri: number; ci: number } | null {
    if (key === null) return null;
    for (let ri = 0; ri < gridRows.length; ri += 1) {
      const cells = gridRows[ri]?.cells ?? [];
      for (let ci = 0; ci < cells.length; ci += 1) {
        if (cells[ci]?.key === key) return { ri, ci };
      }
    }
    return null;
  }

  function moveFrom(key: string | null, dr: number, dc: number): void {
    const pos = locate(key);
    if (!pos) {
      if (firstKey) focusCell(firstKey);
      return;
    }
    let { ri, ci } = pos;
    if (dc !== 0) {
      const cells = gridRows[ri]?.cells ?? [];
      const nc = ci + dc;
      if (nc >= 0 && nc < cells.length) {
        const target = cells[nc];
        if (target) focusCell(target.key);
        return;
      }
      // Wrap across rows at the horizontal edges.
      if (dc < 0 && ri > 0) {
        const prev = gridRows[ri - 1]?.cells ?? [];
        const target = prev[prev.length - 1];
        if (target) focusCell(target.key);
        return;
      }
      if (dc > 0 && ri < gridRows.length - 1) {
        const next = gridRows[ri + 1]?.cells[0];
        if (next) focusCell(next.key);
        return;
      }
      return;
    }
    ri += dr;
    if (ri < 0 || ri >= gridRows.length) return;
    const cells = gridRows[ri]?.cells ?? [];
    const target = cells[Math.min(ci, cells.length - 1)] ?? cells[0];
    if (target) focusCell(target.key);
  }

  function rateableCell(row: CompareRow, colKey: string): boolean {
    const col = columns.find((c) => c.key === colKey);
    const cell = row.cells[colKey];
    if (!col?.log || !col.log.run_id) return false;
    if (!cell || cell.canon === PENDING_CANON || cell.canon === ERROR_CANON) return false;
    return true;
  }

  function jumpNextUnrated(fromKey: string | null): void {
    // Search order: disagreement rows first, then the rest; within a row,
    // columns left to right. Start after the current cell, wrap around.
    const ordered: FocusCell[] = [];
    const disagreements: FocusCell[] = [];
    const rest: FocusCell[] = [];
    for (const gr of gridRows) {
      const isDis = gr.row.state === "majority" || gr.row.state === "split";
      for (const cell of gr.cells) {
        if (rateableCell(cell.row, cell.colKey) && effectiveRating(cell.row, cell.colKey) === null) {
          (isDis ? disagreements : rest).push(cell);
        }
      }
    }
    ordered.push(...disagreements, ...rest);
    if (ordered.length === 0) return;
    const idx = fromKey ? ordered.findIndex((c) => c.key === fromKey) : -1;
    const next = ordered[(idx + 1) % ordered.length];
    if (next && next.key !== fromKey) focusCell(next.key);
  }

  function onCellKeyDown(e: React.KeyboardEvent, cell: FocusCell): void {
    const target = e.target as HTMLElement | null;
    const tag = target?.tagName ?? "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const inButton = !!target?.closest?.("button, a");
    const key = e.key;

    if (key === "ArrowUp") {
      e.preventDefault();
      moveFrom(cell.key, -1, 0);
      return;
    }
    if (key === "ArrowDown") {
      e.preventDefault();
      moveFrom(cell.key, 1, 0);
      return;
    }
    if (key === "ArrowLeft") {
      e.preventDefault();
      moveFrom(cell.key, 0, -1);
      return;
    }
    if (key === "ArrowRight") {
      e.preventDefault();
      moveFrom(cell.key, 0, 1);
      return;
    }
    if (key === "?" ) {
      e.preventDefault();
      setShortcutsOpen(true);
      return;
    }
    if (key === "u" || key === "U" || key === "d" || key === "D") {
      if (!editable) return;
      e.preventDefault();
      const next = key === "u" || key === "U" ? "up" : "down";
      if (rateableCell(cell.row, cell.colKey)) handleCellRate(cell.row, cell.colKey, next);
      else return;
      moveFrom(cell.key, 1, 0);
      return;
    }
    if (key === "x" || key === "X" || key === "0") {
      if (!editable) return;
      e.preventDefault();
      if (rateableCell(cell.row, cell.colKey)) handleCellRate(cell.row, cell.colKey, null);
      return;
    }
    if (key === "r" || key === "R") {
      if (!editable) return;
      e.preventDefault();
      const rating = effectiveRating(cell.row, cell.colKey);
      if (rating === null) return;
      setRemarksFor(cell.key);
      return;
    }
    if (key === "n" || key === "N") {
      e.preventDefault();
      jumpNextUnrated(cell.key);
      return;
    }
    if (key === "Enter" || key === " ") {
      // Let focused buttons activate normally; the cell itself opens the drawer.
      if (inButton) return;
      e.preventDefault();
      setDrawerRow(cell.row);
    }
  }

  if (narrow) {
    return (
      <CompareCardList columns={columns} agents={agents} editable={editable} onRetry={onRetry} showDoneBadge={showDoneBadge} />
    );
  }

  return (
    <div className="grid gap-3">
      <CompareToolbar
        detail={detail}
        onDetail={setDetail}
        onShortcuts={() => setShortcutsOpen(true)}
      />
      <div
        className="max-h-[calc(100svh-140px)] overflow-auto rounded-2xl border border-[#e5e7eb] bg-white dark:border-white/10 dark:bg-[#1a1a1a]"
        onFocus={() => setGridActive(true)}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setGridActive(false);
        }}
      >
        <table ref={gridRef} role="grid" aria-label="Model comparison" className="w-full border-collapse text-left">
          <thead className="sticky top-0 z-20">
            <tr>
              <th
                scope="col"
                className="sticky left-0 z-30 min-w-[180px] border-b border-[#e5e7eb] bg-[#f1f2f3] p-3 font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#C3C2B7]"
              >
                Attribute
              </th>
              {columns.map((col) => {
                const s = perCol.get(col.key) ?? { agreePct: 0, up: 0, down: 0, rated: 0, total: visibleRows.length };
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
                    showDoneBadge={showDoneBadge}
                  />
                );
              })}
            </tr>
          </thead>
          <tbody>
            {agents.map((agent) => {
              const agentRows = rowsByAgent.get(agent.id) ?? [];
              const totalRows = model.rows.filter((r) => r.agentId === agent.id);
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
                      const attrDesc = attrDescription(row);
                      const mark = agreementMark(row);
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
                            const ck = cellKeyFor(row, col.key, false);
                            const focused = gridActive && activeKey === ck;
                            const focusProps = {
                              "data-cell-key": ck,
                              tabIndex: (activeKey === ck ? 0 : -1) as number,
                              onFocus: () => setFocusKey(ck),
                              onKeyDown: (e: React.KeyboardEvent) =>
                                onCellKeyDown(e, { row, colKey: col.key, merged: false, key: ck }),
                            };
                            // Running / queued (no log yet): skeleton shimmer.
                            if (!col.log || col.status === "running" || col.status === "queued") {
                              if (col.status === "error" && !col.log) {
                                return (
                                  <td
                                    key={col.key}
                                    {...focusProps}
                                    className={cn(
                                      "min-w-[220px] border-l-4 border-l-[#ef4444] bg-[#fdecec] p-3 align-top focus-visible:outline-2 focus-visible:outline-brand dark:bg-[#ef4444]/10",
                                      focused && "outline outline-2 outline-[#0d5c4a] outline-offset-[-2px]",
                                    )}
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
                                <td
                                  key={col.key}
                                  {...focusProps}
                                  className={cn(
                                    "min-w-[220px] p-3 align-top focus-visible:outline-2 focus-visible:outline-brand",
                                    focused && "outline outline-2 outline-[#0d5c4a] outline-offset-[-2px]",
                                  )}
                                >
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
                                  {...focusProps}
                                  className={cn(
                                    "min-w-[220px] border-l-4 border-l-[#ef4444] bg-[#fdecec] p-3 align-top focus-visible:outline-2 focus-visible:outline-brand dark:bg-[#ef4444]/10",
                                    focused && "outline outline-2 outline-[#0d5c4a] outline-offset-[-2px]",
                                  )}
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
                                <td
                                  key={col.key}
                                  {...focusProps}
                                  className={cn(
                                    "min-w-[220px] p-3 align-top focus-visible:outline-2 focus-visible:outline-brand",
                                    focused && "outline outline-2 outline-[#0d5c4a] outline-offset-[-2px]",
                                  )}
                                >
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
                              <td
                                key={col.key}
                                {...focusProps}
                                className={cn(
                                  "relative min-w-[220px] p-3 align-top focus-visible:outline-2 focus-visible:outline-brand",
                                  tdClass,
                                  focused && "outline outline-2 outline-[#0d5c4a] outline-offset-[-2px]",
                                )}
                              >
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
                                  onValueClick={() => setDrawerRow(row)}
                                  onRemarksClick={() =>
                                    setRemarksFor((cur) => (cur === ck ? null : ck))
                                  }
                                  remarksOpen={remarksFor === ck}
                                  canRemark={rating !== null}
                                />
                                {remarksFor === ck && (
                                  <RemarksPopover
                                    value={getRemarksText(row, col.key)}
                                    onClose={() => setRemarksFor(null)}
                                    onSave={(text) => {
                                      void saveRemarksFor(row, col.key, text)
                                        .then(() => setRemarksFor(null))
                                        .catch(() => undefined);
                                    }}
                                  />
                                )}
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
      <AttributeCompareDrawer
        row={drawerRow}
        columns={columns}
        agent={drawerAgent}
        open={drawerRow !== null}
        onClose={() => setDrawerRow(null)}
        editable={editable}
        model={model}
      />
      {shortcutsOpen && (
        <Modal title="Keyboard shortcuts" onClose={() => setShortcutsOpen(false)}>
          <ul className="grid gap-2">
            {SHORTCUTS.map(([keys, desc]) => (
              <li key={keys} className="flex items-baseline gap-3 font-sans text-sm">
                <kbd className="min-w-28 shrink-0 rounded-lg border border-[#e5e7eb] bg-[#f1f2f3] px-2 py-1 text-center font-mono text-xs text-[#1d1d1d] dark:border-white/10 dark:bg-white/10 dark:text-[#F0EFEC]">
                  {keys}
                </kbd>
                <span className="text-[#4a5058] dark:text-[#C3C2B7]">{desc}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 font-sans text-xs text-[#8a8f98]">
            Shortcuts are ignored while typing in an input, textarea or select.
          </p>
        </Modal>
      )}
    </div>
  );
}
