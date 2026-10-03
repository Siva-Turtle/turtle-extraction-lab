import * as React from "react";
import { toast } from "sonner";
import { buildRows, columnStats, linkedTargets } from "../../lib/compare";
import type { CompareRow } from "../../lib/compare";
import type { CompareAgent, CompareColumn } from "../../lib/logTypes";
import { useFeedback } from "../../lib/useFeedback";
import type { FeedbackRateItem } from "../../lib/useFeedback";
import type { CompareDetail } from "./CompareToolbar";

const PREFS_KEY = "lab:compare-prefs";

function loadDetail(): CompareDetail {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return "value";
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return "value";
    const rec = parsed as Record<string, unknown>;
    const d = rec.detail;
    if (d === "confidence" || d === "evidence" || d === "value") return d;
    return "value";
  } catch {
    return "value";
  }
}

export function snapshotAgentName(
  log: CompareColumn["log"],
  agentId: string,
  fallback: string,
): string {
  try {
    const snap = (log?.agent_snapshot ?? {})[agentId] as { name?: unknown } | undefined;
    if (snap && typeof snap.name === "string" && snap.name.trim() !== "") return snap.name;
  } catch {
    // fall through
  }
  return fallback;
}

export function comparedKeys(row: CompareRow): string[] {
  const out: string[] = [];
  for (const keys of row.groups.values()) out.push(...keys);
  return out;
}

export function agreementMark(row: CompareRow): string {
  const pct = row.score === null || row.score === undefined ? null : Math.round(row.score * 100);
  if (row.state === "unanimous") return pct === null ? "✓" : `✓ ${pct}%`;
  if (row.state === "majority") {
    if (pct !== null) return `≠ ${pct}%`;
    let compared = 0;
    let largest = 0;
    for (const keys of row.groups.values()) {
      compared += keys.length;
      if (keys.length > largest) largest = keys.length;
    }
    return `≠ ${largest}/${compared}`;
  }
  if (row.state === "split") return pct === null ? "⚡" : `⚡ ${pct}%`;
  if (row.state === "none_found") return "∅";
  return "…";
}

export function scorePct(row: CompareRow): string | null {
  if (row.allEmpty) return null;
  if (row.score === null || row.score === undefined) return null;
  return `${Math.round(row.score * 100)}%`;
}

export type CellIdentity = {
  runId: string;
  agentName: string;
  rating: "up" | "down" | null;
  ratingValue: "up" | "down" | "";
  remarks: string;
};

/**
 * Shared row/feedback model for ComparisonMatrix and CompareCardList.
 * Merge is always OFF (every model gets its own cell); link identical is
 * always ON with Undo toast. Only the detail mode is persisted (stored merge
 * values in `lab:compare-prefs` are ignored).
 */
export function useCompareModel(
  columns: CompareColumn[],
  agents: CompareAgent[],
): {
  rows: CompareRow[];
  visibleRows: CompareRow[];
  rowsByAgent: Map<string, CompareRow[]>;
  detail: CompareDetail;
  setDetail: React.Dispatch<React.SetStateAction<CompareDetail>>;
  disputedFirst: boolean;
  setDisputedFirst: React.Dispatch<React.SetStateAction<boolean>>;
  collapsed: Set<string>;
  toggleAgent: (agentId: string) => void;
  perCol: Map<string, { agreePct: number; up: number; down: number; rated: number; total: number }>;
  cheapestKey: string;
  fastestKey: string;
  allFinished: boolean;
  feedback: ReturnType<typeof useFeedback>;
  effectiveRating: (row: CompareRow, colKey: string) => "up" | "down" | null;
  getRemarksText: (row: CompareRow, colKey: string) => string;
  hasRemarks: (row: CompareRow, colKey: string) => boolean;
  cellIdentity: (row: CompareRow, colKey: string) => CellIdentity | null;
  attrDescription: (row: CompareRow) => string;
  handleCellRate: (row: CompareRow, colKey: string, next: "up" | "down" | null) => void;
  saveRemarksFor: (row: CompareRow, colKey: string, text: string) => Promise<void>;
} {
  const feedback = useFeedback();
  const [detail, setDetail] = React.useState<CompareDetail>(loadDetail);
  const [collapsed, setCollapsed] = React.useState<Set<string>>(new Set());
  const [disputedFirst, setDisputedFirst] = React.useState(false);

  React.useEffect(() => {
    try {
      localStorage.setItem(PREFS_KEY, JSON.stringify({ detail }));
    } catch {
      // ignore persistence failures (private mode, quota)
    }
  }, [detail]);

  const rows = React.useMemo(() => buildRows(agents, columns), [agents, columns]);

  const effectiveRating = React.useCallback(
    (row: CompareRow, colKey: string): "up" | "down" | null => {
      const col = columns.find((c) => c.key === colKey);
      const log = col?.log;
      if (!log || !log.run_id) return null;
      const agentName = snapshotAgentName(log, row.agentId, row.agentName);
      return feedback.getRating(log.run_id, agentName, row.attr, row.cells[colKey]?.feedback ?? null);
    },
    [columns, feedback],
  );

  const getRemarksText = React.useCallback(
    (row: CompareRow, colKey: string): string => {
      const col = columns.find((c) => c.key === colKey);
      const log = col?.log;
      if (!log || !log.run_id) {
        return typeof row.cells[colKey]?.feedback?.remarks === "string"
          ? (row.cells[colKey]?.feedback?.remarks as string)
          : "";
      }
      const agentName = snapshotAgentName(log, row.agentId, row.agentName);
      return feedback.getRemarks(
        log.run_id,
        agentName,
        row.attr,
        row.cells[colKey]?.feedback ?? null,
      );
    },
    [columns, feedback],
  );

  const hasRemarks = React.useCallback(
    (row: CompareRow, colKey: string): boolean => {
      const t = getRemarksText(row, colKey);
      return t.trim() !== "";
    },
    [getRemarksText],
  );

  const cellIdentity = React.useCallback(
    (row: CompareRow, colKey: string): CellIdentity | null => {
      const col = columns.find((c) => c.key === colKey);
      const log = col?.log;
      if (!log || !log.run_id) return null;
      const agentName = snapshotAgentName(log, row.agentId, row.agentName);
      const rating = feedback.getRating(
        log.run_id,
        agentName,
        row.attr,
        row.cells[colKey]?.feedback ?? null,
      );
      const remarks = feedback.getRemarks(
        log.run_id,
        agentName,
        row.attr,
        row.cells[colKey]?.feedback ?? null,
      );
      return {
        runId: log.run_id,
        agentName,
        rating,
        ratingValue: rating ?? "",
        remarks,
      };
    },
    [columns, feedback],
  );

  const attrDescription = React.useCallback(
    (row: CompareRow): string => {
      return (
        agents
          .find((a) => a.id === row.agentId)
          ?.attributes.find((a) => a.name === row.attr)?.description ?? ""
      );
    },
    [agents],
  );

  const perCol = React.useMemo(() => {
    const map = new Map<string, { agreePct: number; up: number; down: number; rated: number; total: number }>();
    for (const col of columns) {
      const base = columnStats(rows, col.key);
      let up = 0;
      let down = 0;
      for (const r of rows) {
        const rating = effectiveRating(r, col.key);
        if (rating === "up") up += 1;
        else if (rating === "down") down += 1;
      }
      map.set(col.key, { agreePct: base.agreePct, up, down, rated: up + down, total: rows.length });
    }
    return map;
  }, [rows, columns, effectiveRating]);

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

  const buildItems = React.useCallback(
    (row: CompareRow, keys: string[], rating: "up" | "down" | ""): FeedbackRateItem[] => {
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
    },
    [columns],
  );

  const rateWithUndo = React.useCallback(
    async (row: CompareRow, keys: string[], rating: "up" | "down" | ""): Promise<void> => {
      const items = buildItems(row, keys, rating);
      if (items.length === 0) return;
      const prevItems: FeedbackRateItem[] = items.map((it) => {
        const col = columns.find((c) => c.log?.run_id === it.runId);
        let prev: "up" | "down" | "" = "";
        if (col) {
          const cur = effectiveRating(row, col.key);
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
    },
    [buildItems, columns, effectiveRating, feedback],
  );

  const handleCellRate = React.useCallback(
    (row: CompareRow, colKey: string, next: "up" | "down" | null): void => {
      const rating: "up" | "down" | "" = next ?? "";
      // Link identical is always ON.
      const keys = [colKey, ...linkedTargets(rows, row, colKey)];
      void rateWithUndo(row, keys, rating);
    },
    [rateWithUndo, rows],
  );

  const saveRemarksFor = React.useCallback(
    async (row: CompareRow, colKey: string, text: string): Promise<void> => {
      const id = cellIdentity(row, colKey);
      if (!id) return;
      await feedback.saveRemarks(id.runId, id.agentName, row.attr, id.ratingValue, text);
    },
    [cellIdentity, feedback],
  );

  function toggleAgent(agentId: string): void {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      return next;
    });
  }

  const visibleRows = React.useMemo(() => {
    if (!disputedFirst) return rows;
    return [...rows].sort((a, b) => {
      const sa = a.score;
      const sb = b.score;
      if (sa === null && sb === null) return a.attr.localeCompare(b.attr);
      if (sa === null) return 1;
      if (sb === null) return -1;
      if (sa !== sb) return (sa as number) - (sb as number);
      return a.attr.localeCompare(b.attr);
    });
  }, [rows, disputedFirst]);

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

  const allFinished =
    columns.length > 0 && columns.every((c) => c.status === "done" || c.status === "partial");

  return {
    rows,
    visibleRows,
    rowsByAgent,
    detail,
    setDetail,
    disputedFirst,
    setDisputedFirst,
    collapsed,
    toggleAgent,
    perCol,
    cheapestKey,
    fastestKey,
    allFinished,
    feedback,
    effectiveRating,
    getRemarksText,
    hasRemarks,
    cellIdentity,
    attrDescription,
    handleCellRate,
    saveRemarksFor,
  };
}

export type CompareModel = ReturnType<typeof useCompareModel>;
