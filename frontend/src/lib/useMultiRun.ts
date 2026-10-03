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

type RetryAgentResponse = {
  log: LogRow;
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

export function agentRunningKey(colKey: string, agentId: string): string {
  return `${colKey}|${agentId}`;
}

function stripAgentIds(payload: Record<string, unknown>): Record<string, unknown> {
  const { agent_ids: _omit, reuse: _reuseOmit, ...rest } = payload;
  return rest;
}

/**
 * Fire one `POST /runs` (or `/runs/auto`) per model slot sharing a
 * `run_group_id`, all at once. Columns stay in picker order and fill in as
 * each call settles. Pass `{ reuse }` for per-agent reuse (slotKey ->
 * agent_id -> log_id, both manual and auto); pass `{ auto: true }` to post
 * `/runs/auto` per slot (payload minus agent_ids, plus `reuse` for that
 * slot). Pass `{ auto: true, reuseLogs }` (slotKey -> log_id) to POST
 * `/runs/reuse` for those columns instead of `/runs/auto`; `retry()` always
 * re-posts `/runs/auto` (fresh). Single-model runs use the same path.
 */
export function useMultiRun(): {
  groupId: string;
  columns: CompareColumn[];
  running: boolean;
  agentRunning: Record<string, boolean>;
  start: (
    slots: ModelSlot[],
    basePayload: Record<string, unknown>,
    opts?: {
      reuse?: Record<string, Record<string, string>>;
      auto?: boolean;
      reuseLogs?: Record<string, string>;
    },
  ) => void;
  retry: (key: string) => void;
  retryAgent: (colKey: string, agentId: string) => void;
} {
  const qc = useQueryClient();
  const [groupId, setGroupId] = React.useState("");
  const [columns, setColumns] = React.useState<CompareColumn[]>([]);
  const [agentRunning, setAgentRunning] = React.useState<Record<string, boolean>>({});
  const payloadRef = React.useRef<Record<string, unknown>>({});
  const groupRef = React.useRef("");
  // Per-column reuse map so retry() re-posts /runs with the same reuse map
  // for that column.
  const reuseRef = React.useRef<Record<string, Record<string, string>>>({});
  const autoRef = React.useRef(false);

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
    opts?: {
      reuse?: Record<string, Record<string, string>>;
      auto?: boolean;
      reuseLogs?: Record<string, string>;
    },
  ) {
    const gid = crypto.randomUUID().replaceAll("-", "");
    const now = Date.now();
    const auto = opts?.auto === true;
    const reuse = opts?.reuse ?? {};
    // SlotKey -> log_id, only honoured when auto is true. Cleaned defensively.
    const reuseLogs: Record<string, string> = {};
    if (auto && opts?.reuseLogs && typeof opts.reuseLogs === "object") {
      for (const [k, v] of Object.entries(opts.reuseLogs)) {
        if (typeof k === "string" && typeof v === "string" && v !== "") {
          reuseLogs[k] = v;
        }
      }
    }
    // Every column POSTs /runs with `reuse` for that slot ({} = fresh).
    const reuseForColumn: Record<string, Record<string, string>> = {};
    const cols: CompareColumn[] = slots.map((s) => {
      const k = slotKey(s.model, s.effort);
      const m = reuse[k];
      const clean: Record<string, string> = {};
      if (m && typeof m === "object") {
        for (const [agentId, logId] of Object.entries(m)) {
          if (typeof agentId === "string" && typeof logId === "string" && logId !== "") {
            clean[agentId] = logId;
          }
        }
      }
      reuseForColumn[k] = clean;
      return {
        key: k,
        model: s.model,
        effort: s.effort,
        status: "running",
        startedAt: now,
        log: null,
      };
    });
    reuseRef.current = reuseForColumn;
    autoRef.current = auto;
    groupRef.current = gid;
    payloadRef.current = { ...basePayload };
    setGroupId(gid);
    setColumns(cols);
    setAgentRunning({});
    if (cols.length === 0) {
      return;
    }
    const jobs = cols.map((col) => {
      const reuseLogId = auto ? reuseLogs[col.key] : undefined;
      if (reuseLogId) {
        return api.post("/runs/reuse", { log_id: reuseLogId, run_group_id: gid }).then(
          (res) => applySuccess(col.key, (res.data as RunResponse).log ?? null),
          (e: unknown) => applyHttpError(col.key, e),
        );
      }
      const body = auto
        ? {
            ...stripAgentIds(basePayload),
            model: col.model,
            reasoning_effort: col.effort,
            run_group_id: gid,
            reuse: reuseForColumn[col.key] ?? {},
          }
        : {
            ...basePayload,
            model: col.model,
            reasoning_effort: col.effort,
            run_group_id: gid,
            reuse: reuseForColumn[col.key] ?? {},
          };
      return api.post(auto ? "/runs/auto" : "/runs", body).then(
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
    const auto = autoRef.current;
    setColumns((prev) =>
      prev.map((c) =>
        c.key === key ? { ...c, status: "running" as const, error: undefined, startedAt: Date.now() } : c,
      ),
    );
    const body = auto
      ? {
          ...stripAgentIds(payloadRef.current),
          model: target.model,
          reasoning_effort: target.effort,
          run_group_id: gid,
        }
      : {
          ...payloadRef.current,
          model: target.model,
          reasoning_effort: target.effort,
          run_group_id: gid,
          reuse: reuseRef.current[key] ?? {},
        };
    api
      .post(auto ? "/runs/auto" : "/runs", body)
      .then(
        (res) => applySuccess(key, (res.data as RunResponse).log ?? null),
        (e: unknown) => applyHttpError(key, e),
      );
  }

  function retryAgent(colKey: string, agentId: string) {
    const target = columns.find((c) => c.key === colKey);
    const logId = target?.log?.id;
    if (!target || !logId) return;
    const k = agentRunningKey(colKey, agentId);
    setAgentRunning((prev) => ({ ...prev, [k]: true }));
    api
      .post(`/runs/logs/${logId}/retry-agent`, { agent_id: agentId })
      .then(
        (res) => {
          const log = (res.data as RetryAgentResponse).log ?? null;
          setAgentRunning((prev) => {
            const next = { ...prev };
            delete next[k];
            return next;
          });
          if (log) {
            applySuccess(colKey, log);
            toast.success("Agent retried");
          } else {
            invalidate();
            toast.error("No log in response");
          }
        },
        (e: unknown) => {
          setAgentRunning((prev) => {
            const next = { ...prev };
            delete next[k];
            return next;
          });
          invalidate();
          toast.error(serverDetail(e));
        },
      );
  }

  const running = columns.some((c) => c.status === "running") || Object.keys(agentRunning).length > 0;

  return { groupId, columns, running, agentRunning, start, retry, retryAgent };
}
