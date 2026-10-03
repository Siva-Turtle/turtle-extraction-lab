import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "./api";
import { serverDetail } from "./format";

export type FeedbackRating = "up" | "down" | "";

export type FeedbackRateItem = {
  runId: string;
  agent: string;
  attr: string;
  rating: FeedbackRating;
};

function overlayKey(runId: string, agent: string, attr: string): string {
  return `${runId}|${agent}|${attr}`;
}

function invalidateFeedbackQueries(qc: ReturnType<typeof useQueryClient>): void {
  qc.invalidateQueries({ queryKey: ["logs"] });
  qc.invalidateQueries({ queryKey: ["logs-all"] });
  qc.invalidateQueries({ queryKey: ["log-group"] });
}

/**
 * Optimistic thumbs overlay layered over each log's saved feedback.
 * Posts via `POST /runs/feedback-batch` (single items use batch too).
 * Remarks post via `POST /runs/{run_id}/feedback` with the cell's current
 * rating, with an optimistic remarks overlay.
 */
export function useFeedback(): {
  getRating: (
    runId: string,
    agent: string,
    attr: string,
    saved: { rating: string; remarks: string; auto?: boolean } | null,
  ) => "up" | "down" | null;
  getRemarks: (
    runId: string,
    agent: string,
    attr: string,
    saved: { rating: string; remarks: string; auto?: boolean } | null,
  ) => string;
  isAuto: (
    runId: string,
    agent: string,
    attr: string,
    saved: { rating: string; remarks: string; auto?: boolean } | null,
  ) => boolean;
  rate: (items: FeedbackRateItem[]) => Promise<void>;
  saveRemarks: (
    runId: string,
    agent: string,
    attr: string,
    rating: "up" | "down" | "",
    text: string,
  ) => Promise<void>;
} {
  const qc = useQueryClient();
  const [overlay, setOverlay] = React.useState<Record<string, FeedbackRating>>({});
  const overlayRef = React.useRef(overlay);
  overlayRef.current = overlay;
  const [remarksOverlay, setRemarksOverlay] = React.useState<Record<string, string>>({});
  const remarksOverlayRef = React.useRef(remarksOverlay);
  remarksOverlayRef.current = remarksOverlay;

  const isAuto = React.useCallback(
    (
      runId: string,
      agent: string,
      attr: string,
      saved: { rating: string; remarks: string; auto?: boolean } | null,
    ): boolean => {
      const k = overlayKey(runId, agent, attr);
      if (k in overlay) return false;
      return saved?.auto === true;
    },
    [overlay],
  );

  const getRating = React.useCallback(
    (
      runId: string,
      agent: string,
      attr: string,
      saved: { rating: string; remarks: string; auto?: boolean } | null,
    ): "up" | "down" | null => {
      const k = overlayKey(runId, agent, attr);
      if (k in overlay) {
        const v = overlay[k];
        return v === "up" || v === "down" ? v : null;
      }
      const r = saved?.rating;
      return r === "up" || r === "down" ? r : null;
    },
    [overlay],
  );

  const getRemarks = React.useCallback(
    (
      runId: string,
      agent: string,
      attr: string,
      saved: { rating: string; remarks: string; auto?: boolean } | null,
    ): string => {
      const k = overlayKey(runId, agent, attr);
      if (k in remarksOverlay) return remarksOverlay[k] ?? "";
      return typeof saved?.remarks === "string" ? saved.remarks : "";
    },
    [remarksOverlay],
  );

  const rate = React.useCallback(
    async (items: FeedbackRateItem[]): Promise<void> => {
      if (items.length === 0) return;
      const prev = new Map<string, FeedbackRating | undefined>();
      for (const it of items) {
        const k = overlayKey(it.runId, it.agent, it.attr);
        if (!prev.has(k)) {
          prev.set(k, overlayRef.current[k]);
        }
      }
      setOverlay((prevOv) => {
        const next = { ...prevOv };
        for (const it of items) {
          next[overlayKey(it.runId, it.agent, it.attr)] = it.rating;
        }
        return next;
      });
      try {
        const res = await api.post("/runs/feedback-batch", {
          items: items.map((it) => ({
            run_id: it.runId,
            agent_name: it.agent,
            attribute_name: it.attr,
            rating: it.rating,
          })),
        });
        // Reused cells redirect to the ORIGINAL log: mirror the optimistic
        // overlay under the resolved (source) key too, so the source view
        // shows it immediately. Display for reused cells already reads the
        // merged source feedback, so the original key keeps working.
        try {
          const resolved = (res?.data as { resolved?: unknown })?.resolved;
          if (Array.isArray(resolved)) {
            setOverlay((prevOv) => {
              const next = { ...prevOv };
              for (const r of resolved) {
                if (!r || typeof r !== "object") continue;
                const rec = r as Record<string, unknown>;
                const origId = typeof rec.run_id === "string" ? rec.run_id : "";
                const resId = typeof rec.resolved_run_id === "string" ? rec.resolved_run_id : "";
                const ag = typeof rec.agent_name === "string" ? rec.agent_name : "";
                // Backend resolves by agent_name; items use `agent`. Match by order fallback.
                if (origId === "" || resId === "" || resId === origId) continue;
                // Find the original item to get attr (backend echoes it).
                const attr = typeof rec.attribute_name === "string" ? rec.attribute_name : "";
                // Overlay under the source key with the same rating.
                const srcItem = items.find(
                  (it) => it.runId === origId && it.attr === attr,
                );
                const rating = srcItem ? srcItem.rating : undefined;
                if (rating === undefined) continue;
                // Agent name for the source may differ; use echoed agent_name.
                next[overlayKey(resId, ag, attr)] = rating;
              }
              return next;
            });
          }
        } catch {
          // ignore overlay-mirror failures (main overlay already set)
        }
        invalidateFeedbackQueries(qc);
      } catch (e: unknown) {
        setOverlay((prevOv) => {
          const next = { ...prevOv };
          for (const [k, v] of prev) {
            if (v === undefined) delete next[k];
            else next[k] = v;
          }
          return next;
        });
        toast.error(serverDetail(e));
        throw e;
      }
    },
    [qc],
  );

  const saveRemarks = React.useCallback(
    async (
      runId: string,
      agent: string,
      attr: string,
      rating: "up" | "down" | "",
      text: string,
    ): Promise<void> => {
      const k = overlayKey(runId, agent, attr);
      const prev = remarksOverlayRef.current[k];
      setRemarksOverlay((prevOv) => ({ ...prevOv, [k]: text }));
      try {
        const res = await api.post(`/runs/${runId}/feedback`, {
          agent_name: agent,
          attribute_name: attr,
          rating,
          remarks: text,
        });
        // Mirror remarks overlay under the resolved (source) key for reused cells.
        try {
          const resolvedId = (res?.data as { resolved_run_id?: unknown })?.resolved_run_id;
          if (typeof resolvedId === "string" && resolvedId !== "" && resolvedId !== runId) {
            // Backend echoes the target agent name only via overlayKey agnostic path;
            // reuse the same agent/attr (names are stable across reuse).
            setRemarksOverlay((prevOv) => ({ ...prevOv, [overlayKey(resolvedId, agent, attr)]: text }));
          }
        } catch {
          // ignore mirror failures
        }
        invalidateFeedbackQueries(qc);
      } catch (e: unknown) {
        setRemarksOverlay((prevOv) => {
          const next = { ...prevOv };
          if (prev === undefined) delete next[k];
          else next[k] = prev;
          return next;
        });
        toast.error(serverDetail(e));
        throw e;
      }
    },
    [qc],
  );

  return { getRating, getRemarks, isAuto, rate, saveRemarks };
}
