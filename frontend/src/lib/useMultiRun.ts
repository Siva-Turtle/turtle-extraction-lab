import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "./api";
import { serverDetail } from "./format";
import type { CompareColumn, LogRow, ModelSlot } from "./logTypes";

type RunResponse = {
  id: string;
  outputs?: Record<string, unknown>;
  log?: LogRow | null;
};

/** done when no agent failed, partial when some did, error when all did. */
function statusOf(log: LogRow): CompareColumn["status"] {
  const outputs = (log.outputs ?? {}) as Record<string, unknown>;
  const entries = Object.values(outputs);
  if (entries.length === 0) return "done";
  let failed = 0;
  for (const out of entries) {
    if (out && typeof out === "object" && !Array.isArray(out) && "_error" in out) failed += 1;
  }
  if (failed === 0) return "done";
  if (failed === entries.length) return "error";
  return "partial";
}

function slotKey(model: string, effort: string): string {
  return `${model}|${effort}`;
}

/**
 * Fire one `POST /runs` per model slot sharing a `run_group_id`, all at
 * once. Columns stay in picker order and fill in as each call settles.
 * Pass `{ existing }` to save a reused copy for matched slots: those columns
 * `POST /runs/reuse` (`{log_id, run_group_id}`) in parallel with the real
 * runs, starting "running" and settling from `res.data.log` exactly like a
 * real run (so the copy appears in Log history in the same group).
 */
export function useMultiRun(): {
  groupId: string;
  columns: CompareColumn[];
  running: boolean;
  start: (
    slots: ModelSlot[],
    basePayload: Record<string, unknown>,
    opts?: { existing?: Record<string, LogRow> },
  ) => void;
  retry: (key: string) => void;
} {
  const qc = useQueryClient();
  const [groupId, setGroupId] = React.useState("");
  const [columns, setColumns] = React.useState<CompareColumn[]>([]);
  const payloadRef = React.useRef<Record<string, unknown>>({});
  const groupRef = React.useRef("");
  // Per-column flag: which columns are reuse copies + the source log id, so
  // retry() re-posts to /runs/reuse instead of firing a fresh model run.
  const reuseSourceRef = React.useRef<Record<string, string>>({});

  const invalidate = React.useCallback(() => {
    qc.invalidateQueries({ queryKey: ["logs"] });
    qc.invalidateQueries({ queryKey: ["logs-all"] });
  }, [qc]);

  function applySuccess(key: string, log: LogRow | null) {
    const status: CompareColumn["status"] = log ? statusOf(log) : "error";
    setColumns((prev) =>
      prev.map((c) =>
        c.key === key
          ? { ...c, status, log, error: log ? undefined : "No log in response" }
          : c,
      ),
    );
    invalidate();
    return status;
  }

  function applyHttpError(key: string, e: unknown) {
    setColumns((prev) =>
      prev.map((c) => (c.key === key ? { ...c, status: "error" as const, error: serverDetail(e) } : c)),
    );
    invalidate();
    return "error" as const;
  }

  function start(
    slots: ModelSlot[],
    basePayload: Record<string, unknown>,
    opts?: { existing?: Record<string, LogRow> },
  ) {
    const gid = crypto.randomUUID().replaceAll("-", "");
    const now = Date.now();
    const existing = opts?.existing ?? {};
    // Every column starts "running" — matched slots POST /runs/reuse, the
    // rest POST /runs — so the reused copy lands in Log history in this
    // group with its cost/time/tokens. Single-model runs use the same path.
    const reuseSource: Record<string, string> = {};
    const cols: CompareColumn[] = slots.map((s) => {
      const k = slotKey(s.model, s.effort);
      const old = existing[k];
      if (old && typeof old.id === "string" && old.id !== "") {
        reuseSource[k] = old.id;
      }
      return {
        key: k,
        model: s.model,
        effort: s.effort,
        status: "running",
        startedAt: now,
        log: null,
      };
    });
    reuseSourceRef.current = reuseSource;
    groupRef.current = gid;
    payloadRef.current = { ...basePayload };
    setGroupId(gid);
    setColumns(cols);
    if (cols.length === 0) {
      return;
    }
    const jobs = cols.map((col) => {
      const sourceId = reuseSource[col.key];
      if (sourceId) {
        return api
          .post("/runs/reuse", { log_id: sourceId, run_group_id: gid })
          .then(
            (res) => applySuccess(col.key, (res.data as RunResponse).log ?? null),
            (e: unknown) => applyHttpError(col.key, e),
          );
      }
      return api
        .post("/runs", {
          ...basePayload,
          model: col.model,
          reasoning_effort: col.effort,
          run_group_id: gid,
        })
        .then(
          (res) => applySuccess(col.key, (res.data as RunResponse).log ?? null),
          (e: unknown) => applyHttpError(col.key, e),
        );
    });
    void Promise.all(jobs).then((statuses) => {
      const ok = statuses.filter((s) => s === "done" || s === "partial").length;
      const failed = statuses.length - ok;
      const total = cols.length;
      if (failed === 0) toast.success(`${ok}/${total} models done`);
      else toast.error(`${ok}/${total} done · ${failed} failed`);
    });
  }

  function retry(key: string) {
    const target = columns.find((c) => c.key === key);
    const gid = groupRef.current;
    if (!target || !gid) return;
    setColumns((prev) =>
      prev.map((c) =>
        c.key === key ? { ...c, status: "running" as const, error: undefined, startedAt: Date.now() } : c,
      ),
    );
    const sourceId = reuseSourceRef.current[key];
    if (sourceId) {
      api.post("/runs/reuse", { log_id: sourceId, run_group_id: gid }).then(
        (res) => applySuccess(key, (res.data as RunResponse).log ?? null),
        (e: unknown) => applyHttpError(key, e),
      );
      return;
    }
    api
      .post("/runs", {
        ...payloadRef.current,
        model: target.model,
        reasoning_effort: target.effort,
        run_group_id: gid,
      })
      .then(
        (res) => applySuccess(key, (res.data as RunResponse).log ?? null),
        (e: unknown) => applyHttpError(key, e),
      );
  }

  const running = columns.some((c) => c.status === "running");

  return { groupId, columns, running, start, retry };
}
