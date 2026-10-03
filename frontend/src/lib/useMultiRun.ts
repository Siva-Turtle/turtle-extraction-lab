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
 * Pass `{ existing }` to reuse already-finished logs for matched slots
 * (those columns are not posted; ratings go to the old log's run_id).
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
    const cols: CompareColumn[] = slots.map((s) => {
      const k = slotKey(s.model, s.effort);
      const old = existing[k];
      if (old) {
        return {
          key: k,
          model: s.model,
          effort: s.effort,
          status: statusOf(old),
          log: old,
        };
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
    groupRef.current = gid;
    payloadRef.current = { ...basePayload };
    setGroupId(gid);
    setColumns(cols);
    const toRun = cols.filter((c) => !existing[c.key]);
    // All columns came from existing logs: nothing to post.
    if (toRun.length === 0) {
      invalidate();
      const ok = cols.filter((c) => c.status === "done" || c.status === "partial").length;
      const failed = cols.length - ok;
      if (failed === 0) toast.success(`${ok}/${cols.length} models done`);
      else toast.error(`${ok}/${cols.length} done · ${failed} failed`);
      return;
    }
    const jobs = toRun.map((col) =>
      api
        .post("/runs", {
          ...basePayload,
          model: col.model,
          reasoning_effort: col.effort,
          run_group_id: gid,
        })
        .then(
          (res) => applySuccess(col.key, (res.data as RunResponse).log ?? null),
          (e: unknown) => applyHttpError(col.key, e),
        ),
    );
    void Promise.all(jobs).then((statuses) => {
      const existingOk = cols.filter(
        (c) => existing[c.key] && (c.status === "done" || c.status === "partial"),
      ).length;
      const existingFailed = cols.filter(
        (c) => existing[c.key] && c.status !== "done" && c.status !== "partial",
      ).length;
      const ok = statuses.filter((s) => s === "done" || s === "partial").length + existingOk;
      const failed = statuses.length - (statuses.filter((s) => s === "done" || s === "partial").length) + existingFailed;
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
