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

/**
 * Optimistic thumbs overlay layered over each log's saved feedback.
 * Posts via `POST /runs/feedback-batch` (single items use batch too).
 */
export function useFeedback(): {
  getRating: (
    runId: string,
    agent: string,
    attr: string,
    saved: { rating: string; remarks: string } | null,
  ) => "up" | "down" | null;
  getRemarks: (saved: { rating: string; remarks: string } | null) => string;
  rate: (items: FeedbackRateItem[]) => Promise<void>;
} {
  const qc = useQueryClient();
  const [overlay, setOverlay] = React.useState<Record<string, FeedbackRating>>({});
  const overlayRef = React.useRef(overlay);
  overlayRef.current = overlay;

  const getRating = React.useCallback(
    (
      runId: string,
      agent: string,
      attr: string,
      saved: { rating: string; remarks: string } | null,
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
    (saved: { rating: string; remarks: string } | null): string => {
      return typeof saved?.remarks === "string" ? saved.remarks : "";
    },
    [],
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
        await api.post("/runs/feedback-batch", {
          items: items.map((it) => ({
            run_id: it.runId,
            agent_name: it.agent,
            attribute_name: it.attr,
            rating: it.rating,
          })),
        });
        qc.invalidateQueries({ queryKey: ["logs"] });
        qc.invalidateQueries({ queryKey: ["logs-all"] });
        qc.invalidateQueries({ queryKey: ["log-group"] });
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

  return { getRating, getRemarks, rate };
}
