import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "./api";
import { sortCompareColumns } from "./compare";
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

type StreamStart = {
  type: "start";
  run_id: string;
  log_id?: string;
  model: string;
  agents: { agent_id: string; agent_name: string }[];
};

type StreamAgent = {
  type: "agent";
  agent_id: string;
  agent_name: string;
  output: unknown;
  usage: Record<string, unknown>;
  status: "done" | "error";
  error?: string;
  reused?: boolean;
};

type StreamIdentifier = {
  type: "identifier";
  agent_id: string;
  agent_name: string;
  output: unknown;
  usage: Record<string, unknown>;
  answers?: Record<string, boolean>;
  plan?: unknown[];
  status: "done" | "error";
  error?: string;
};

type StreamDone = {
  type: "done";
  log: LogRow;
  id?: string;
  log_id?: string;
  run_group_id?: string;
};

type StreamError = { type: "error"; detail: string };

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

function slotKey(model: string, effort: string, provider: string): string {
  return `${model}|${effort}|${provider ?? ""}`;
}

export function agentRunningKey(colKey: string, agentId: string): string {
  return `${colKey}|${agentId}`;
}

function stripAgentIds(payload: Record<string, unknown>): Record<string, unknown> {
  const { agent_ids: _omit, reuse: _reuseOmit, ...rest } = payload;
  return rest;
}

function apiBase(): string {
  try {
    const b = (api.defaults as { baseURL?: string }).baseURL;
    if (typeof b === "string" && b !== "") return b;
  } catch {
    // fall through
  }
  return "/api/v1";
}

function sumUsage(perAgent: Record<string, Record<string, unknown>>): Record<string, unknown> {
  let prompt = 0;
  let completion = 0;
  let reasoning = 0;
  for (const v of Object.values(perAgent)) {
    try {
      prompt += Number((v as Record<string, unknown>).prompt_tokens ?? 0) || 0;
      completion += Number((v as Record<string, unknown>).completion_tokens ?? 0) || 0;
      reasoning += Number((v as Record<string, unknown>).reasoning_tokens ?? 0) || 0;
    } catch {
      // ignore malformed entries
    }
  }
  return {
    prompt_tokens: prompt,
    completion_tokens: completion,
    total_tokens: prompt + completion,
    reasoning_tokens: reasoning,
    cost_usd: null,
    input_cost_usd: null,
    output_cost_usd: null,
    duration_ms: 0,
    model: "",
    per_agent: perAgent,
    reused_agents: {},
  };
}

function provisionalLog(
  col: CompareColumn,
  runId: string,
  logId: string,
  model: string,
  agents: { agent_id: string; agent_name: string }[],
  outputs: Record<string, unknown>,
  perAgent: Record<string, Record<string, unknown>>,
  extra?: { consistency?: unknown },
): LogRow {
  const snap: Record<string, { name: string }> = {};
  for (const a of agents) {
    snap[a.agent_id] = { name: a.agent_name };
  }
  const usage = sumUsage(perAgent) as LogRow["usage"];
  if (usage && typeof usage === "object") {
    (usage as Record<string, unknown>).model = model;
  }
  return {
    id: logId || `provisional-${col.key}`,
    run_id: runId || `provisional-${col.key}`,
    input_type: "",
    input_data: "",
    model,
    agent_snapshot: snap,
    attribute_snapshot: {},
    outputs: { ...outputs },
    requests: {},
    feedback: {},
    consistency: (extra?.consistency as LogRow["consistency"]) ?? null,
    usage,
    filters: {},
    client: "",
    meeting_type: "",
    meeting_title: "",
    reasoning_effort: col.effort ?? "",
    provider: col.provider ?? "",
    created_at: new Date().toISOString(),
    run_group_id: "",
  } as LogRow;
}

async function postStream(
  path: string,
  body: Record<string, unknown>,
  onEvent: (ev: StreamStart | StreamAgent | StreamIdentifier | StreamDone | StreamError) => void,
): Promise<void> {
  const url = `${apiBase()}${path}`;
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  try {
    const common = (api.defaults as { headers?: { common?: Record<string, string> } }).headers
      ?.common;
    if (common) {
      for (const [k, v] of Object.entries(common)) {
        if (typeof v === "string" && k.toLowerCase() !== "content-type") headers[k] = v;
      }
    }
  } catch {
    // ignore header merge failures
  }
  let resp: Response;
  try {
    resp = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  } catch (e: unknown) {
    throw new Error(serverDetail(e));
  }
  if (!resp.ok || !resp.body) {
    let detail = `Request failed (${resp.status})`;
    try {
      const text = await resp.text();
      if (text) {
        try {
          const parsed = JSON.parse(text) as { detail?: unknown };
          if (typeof parsed?.detail === "string" && parsed.detail !== "") detail = parsed.detail;
          else detail = text.slice(0, 500);
        } catch {
          detail = text.slice(0, 500);
        }
      }
    } catch {
      // keep default
    }
    throw new Error(detail);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let sawEvent = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (value) buf += decoder.decode(value, { stream: !done });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      const t = line.trim();
      if (t === "") continue;
      let ev: StreamStart | StreamAgent | StreamIdentifier | StreamDone | StreamError;
      try {
        ev = JSON.parse(t) as
          | StreamStart
          | StreamAgent
          | StreamIdentifier
          | StreamDone
          | StreamError;
      } catch {
        continue;
      }
      sawEvent = true;
      if (ev && typeof ev === "object" && (ev as { type?: string }).type === "error") {
        throw new Error((ev as StreamError).detail || "Run failed");
      }
      onEvent(ev);
    }
    if (done) break;
  }
  const tail = buf.trim();
  if (tail !== "") {
    try {
      const ev = JSON.parse(tail) as
        | StreamStart
        | StreamAgent
        | StreamIdentifier
        | StreamDone
        | StreamError;
      sawEvent = true;
      if ((ev as { type?: string }).type === "error") {
        throw new Error((ev as StreamError).detail || "Run failed");
      }
      onEvent(ev);
    } catch (e: unknown) {
      if (e instanceof Error && e.message !== "") throw e;
    }
  }
  if (!sawEvent) throw new Error("Empty streaming response");
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
 *
 * Streaming: every `/runs` and `/runs/auto` call sends `stream: true` and
 * renders each agent as its NDJSON event arrives (provisional log with
 * summed usage); the final `done` log replaces it. Thumbs stay disabled
 * until the final log arrives (column status running). `/runs/reuse` and
 * per-agent retry stay non-streaming.
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

  function applyProvisional(
    key: string,
    log: LogRow,
  ) {
    setColumns((prev) =>
      prev.map((c) => (c.key === key ? { ...c, status: "running" as const, log } : c)),
    );
  }

  function runStreamColumn(
    col: CompareColumn,
    path: "/runs" | "/runs/auto",
    body: Record<string, unknown>,
  ): Promise<CompareColumn["status"]> {
    const withStream = { ...body, stream: true };
    let startAgents: { agent_id: string; agent_name: string }[] = [];
    let runId = "";
    let logId = "";
    let model = col.model;
    const outputs: Record<string, unknown> = {};
    const perAgent: Record<string, Record<string, unknown>> = {};
    let consistency: unknown = null;
    let sawAny = false;
    const pushProvisional = () => {
      if (!sawAny) return;
      const log = provisionalLog(col, runId, logId, model, startAgents, outputs, perAgent, {
        consistency,
      });
      applyProvisional(col.key, log);
    };
    return postStream(
      path,
      withStream,
      (ev) => {
        if (!ev || typeof ev !== "object") return;
        const t = (ev as { type?: string }).type;
        if (t === "start") {
          const s = ev as StreamStart;
          sawAny = true;
          runId = typeof s.run_id === "string" ? s.run_id : runId;
          logId = typeof s.log_id === "string" && s.log_id !== "" ? s.log_id : logId;
          if (typeof s.model === "string" && s.model !== "") model = s.model;
          if (Array.isArray(s.agents)) startAgents = s.agents;
          pushProvisional();
        } else if (t === "identifier") {
          const idEv = ev as StreamIdentifier;
          sawAny = true;
          if (idEv.answers || idEv.plan) {
            consistency = {
              auto: true,
              version: 2,
              answers: idEv.answers ?? {},
              plan: idEv.plan ?? [],
              agents: {},
            };
          }
          if (typeof idEv.agent_id === "string" && idEv.agent_id !== "") {
            outputs[idEv.agent_id] = idEv.output;
            perAgent[idEv.agent_id] = (idEv.usage ?? {}) as Record<string, unknown>;
          }
          pushProvisional();
        } else if (t === "agent") {
          const a = ev as StreamAgent;
          sawAny = true;
          if (typeof a.agent_id === "string" && a.agent_id !== "") {
            outputs[a.agent_id] = a.output;
            perAgent[a.agent_id] = (a.usage ?? {}) as Record<string, unknown>;
            if (!startAgents.some((s) => s.agent_id === a.agent_id)) {
              startAgents = [
                ...startAgents,
                { agent_id: a.agent_id, agent_name: a.agent_name || a.agent_id },
              ];
            }
          }
          pushProvisional();
        } else if (t === "done") {
          const d = ev as StreamDone;
          sawAny = true;
          if (d.log) applySuccess(col.key, d.log);
          else applyHttpError(col.key, new Error("No log in response"));
        }
      },
    ).then(
      () => {
        // postStream resolves after the last event; done already applied.
        // If no done arrived (should not happen), surface an error only when
        // nothing was ever shown.
        let st: CompareColumn["status"] = "done";
        setColumns((prev) => {
          const found = prev.find((c) => c.key === col.key);
          if (found) st = found.status;
          return prev;
        });
        return st;
      },
      (e: unknown) => {
        if (!sawAny) return applyHttpError(col.key, e);
        // Streaming failed mid-run after showing partial agents: mark the
        // column failed but keep the provisional log so completed agents
        // stay visible.
        const msg = e instanceof Error ? e.message : serverDetail(e);
        setColumns((prev) =>
          prev.map((c) =>
            c.key === col.key ? { ...c, status: "error" as const, error: msg } : c,
          ),
        );
        invalidate();
        return "error" as const;
      },
    );
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
    // Columns are always alphabetical by model (stable, not slot order).
    const reuseForColumn: Record<string, Record<string, string>> = {};
    const cols: CompareColumn[] = sortCompareColumns(
      slots.map((s) => {
        const k = slotKey(s.model, s.effort, s.provider ?? "");
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
          provider: s.provider ?? "",
          status: "running",
          startedAt: now,
          log: null,
        };
      }),
    );
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
            provider: col.provider ?? "",
            run_group_id: gid,
            reuse: reuseForColumn[col.key] ?? {},
          }
        : {
            ...basePayload,
            model: col.model,
            reasoning_effort: col.effort,
            provider: col.provider ?? "",
            run_group_id: gid,
            reuse: reuseForColumn[col.key] ?? {},
          };
      return runStreamColumn(col, auto ? "/runs/auto" : "/runs", body);
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
          provider: target.provider ?? "",
          run_group_id: gid,
        }
      : {
          ...payloadRef.current,
          model: target.model,
          reasoning_effort: target.effort,
          provider: target.provider ?? "",
          run_group_id: gid,
          reuse: reuseRef.current[key] ?? {},
        };
    void runStreamColumn(target, auto ? "/runs/auto" : "/runs", body);
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
