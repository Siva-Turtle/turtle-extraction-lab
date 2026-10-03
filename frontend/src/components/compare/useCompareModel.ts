import * as React from "react";
import { toast } from "sonner";
import { buildRows, columnStats, groupAgreementPct, linkedTargets } from "../../lib/compare";
import type { CompareRow } from "../../lib/compare";
import type { CompareAgent, CompareColumn } from "../../lib/logTypes";
import { useFeedback } from "../../lib/useFeedback";
import type { FeedbackRateItem } from "../../lib/useFeedback";
import type { CompareDetail, CompareFilter } from "./CompareToolbar";

const PREFS_KEY = "lab:compare-prefs";

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

export type CellIdentity = {
  runId: string;
  agentName: string;
  rating: "up" | "down" | null;
  ratingValue: "up" | "down" | "";
  remarks: string;
};

/**
 * Shared row/filter/feedback model for ComparisonMatrix and CompareCardList.
 * Row building, toolbar state, per-column stats and linked-rating logic live
 * here so the two views cannot drift apart.
 */
export function useCompareModel(
  columns: CompareColumn[],
  agents: CompareAgent[],
): {
  rows: CompareRow[];
  visibleRows: CompareRow[];
  rowsByAgent: Map<string, CompareRow[]>;
  prefs: Prefs;
  setPrefs: React.Dispatch<React.SetStateAction<Prefs>>;
  filter: CompareFilter;
  setFilter: React.Dispatch<React.SetStateAction<CompareFilter>>;
  detail: CompareDetail;
  setDetail: React.Dispatch<React.SetStateAction<CompareDetail>>;
  collapsed: Set<string>;
  toggleAgent: (agentId: string) => void;
  counts: { all: number; disagreements: number; unrated: number; ratedDown: number };
  overall: { up: number; down: number; agreePct: number };
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
  handleMergedRate: (row: CompareRow, next: "up" | "down" | null) => void;
  saveRemarksFor: (row: CompareRow, colKey: string, text: string) => Promise<void>;
} {
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
        const rating = effectiveRating(r, k);
        if (rating === "up" || rating === "down") anyRated = true;
        if (rating === "down") anyDown = true;
      }
      if (!anyRated) unrated += 1;
      if (anyDown) ratedDown += 1;
    }
    return { all: rows.length, disagreements, unrated, ratedDown };
  }, [rows, effectiveRating]);

  const visibleRows = React.useMemo(() => {
    if (filter === "all") return rows;
    return rows.filter((r) => {
      if (filter === "disagreements") return r.state === "majority" || r.state === "split";
      const keys = comparedKeys(r);
      if (filter === "unrated") {
        if (keys.length === 0) return false;
        return keys.every((k) => effectiveRating(r, k) === null);
      }
      if (filter === "rated-down") {
        return keys.some((k) => effectiveRating(r, k) === "down");
      }
      return true;
    });
  }, [rows, filter, effectiveRating]);

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
      const keys = prefs.link ? [colKey, ...linkedTargets(rows, row, colKey)] : [colKey];
      void rateWithUndo(row, keys, rating);
    },
    [prefs.link, rateWithUndo, rows],
  );

  const handleMergedRate = React.useCallback(
    (row: CompareRow, next: "up" | "down" | null): void => {
      const rating: "up" | "down" | "" = next ?? "";
      void rateWithUndo(
        row,
        columns.map((c) => c.key),
        rating,
      );
    },
    [columns, rateWithUndo],
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
    prefs,
    setPrefs,
    filter,
    setFilter,
    detail,
    setDetail,
    collapsed,
    toggleAgent,
    counts,
    overall,
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
    handleMergedRate,
    saveRemarksFor,
  };
}

export type CompareModel = ReturnType<typeof useCompareModel>;
