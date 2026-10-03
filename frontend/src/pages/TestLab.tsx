import * as React from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, ChevronDown, Eye, Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { api, meetingTypeOf } from "../lib/api";
import type { ModelInfo } from "../lib/api";
import { fmtCostBoth, fmtRelative, modelLabel, serverDetail } from "../lib/format";
import { unionAgentsFromLogs } from "../lib/compareData";
import { estimateRunCost } from "../lib/estimate";
import type { ColumnStatus, CompareAgent, CompareColumn, LogRow, ModelSlot } from "../lib/logTypes";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardTitle } from "../components/ui/Card";
import { MultiSelectFilter, SingleSelectFilter } from "../components/ui/Combobox";
import { Modal, fieldInput, fieldLabel } from "../components/ui/Modal";
import { ModelSlotsPicker } from "../components/run/ModelSlotsPicker";
import { ComparisonMatrix } from "../components/compare/ComparisonMatrix";
import { SingleModelTable } from "../components/compare/SingleModelTable";
import { useMultiRun } from "../lib/useMultiRun";
import type { Agent } from "./Agents";

type MeetingClient = { id: string; name: string };
type MeetingSummary = {
  id: string;
  title: string;
  date: string;
  duration_min: number;
  participants: string[];
};
type TranscriptResponse = { id: string; title: string; date: string; transcription: string; scrubbed: boolean };

/** Answers "is the API key loaded" without ever exposing the key itself. */
function KeyStatusBadge() {
  const { data } = useQuery({
    queryKey: ["lab-config"],
    queryFn: async () =>
      (await api.get("/config")).data as { openrouter_configured: boolean; db_ok: boolean },
    staleTime: 60 * 1000,
  });
  if (!data) return null;
  return data.openrouter_configured ? (
    <Badge tone="success">key set</Badge>
  ) : (
    <Badge tone="danger">no key</Badge>
  );
}

function formatMeetingDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type FilterOption = { value: string; label: string; sub?: string };

/**
 * Strict single-select dropdown (no free text) mirroring the ModelCombobox
 * v2 styling: chevron trigger, listbox dropdown, highlight, dark mode.
 */
function FilterCombobox({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  ariaLabel,
}: {
  value: string;
  onChange: (v: string) => void;
  options: FilterOption[];
  placeholder: string;
  disabled?: boolean;
  ariaLabel: string;
}): React.JSX.Element {
  const [open, setOpen] = React.useState(false);
  const [highlight, setHighlight] = React.useState(0);
  const rootRef = React.useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  React.useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  React.useEffect(() => {
    setHighlight(0);
  }, [value, options.length]);

  const hasClear = value !== "";
  const total = options.length + (hasClear ? 1 : 0);

  function pick(v: string) {
    onChange(v);
    setOpen(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (disabled) return;
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => (total === 0 ? 0 : Math.min(h + 1, total - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlight < options.length && options[highlight]) pick(options[highlight].value);
      else if (hasClear) pick("");
    }
  }

  return (
    <div ref={rootRef} className="relative mt-1.5">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKey}
        role="combobox"
        aria-expanded={open}
        aria-label={ariaLabel}
        className={cn(
          "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-2 font-sans text-sm",
          "hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1",
          "dark:border-white/10 dark:bg-[#2e2e2e] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-left",
            selected ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98] dark:text-[#898781]",
          )}
        >
          {selected ? selected.label : placeholder}
        </span>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10">
          <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
        </span>
      </button>
      {open && !disabled && (
        <ul
          role="listbox"
          aria-label={ariaLabel}
          className="absolute inset-x-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-2xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] animate-[turtle-fade-in_120ms_ease-out] dark:border-white/10 dark:bg-[#1a1a1a]"
        >
          {options.map((o, i) => (
            <li key={o.value} role="option" aria-selected={value === o.value}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(o.value)}
                onMouseEnter={() => setHighlight(i)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left font-sans text-sm",
                  i === highlight
                    ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                    : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{o.label}</span>
                  {o.sub && (
                    <span className="block truncate text-xs text-[#4a5058] dark:text-[#C3C2B7]">{o.sub}</span>
                  )}
                </span>
                {value === o.value && (
                  <Check className="h-4 w-4 shrink-0 text-[#0d5c4a] dark:text-[#2fdebf]" aria-hidden="true" />
                )}
              </button>
            </li>
          ))}
          {hasClear && (
            <li role="option" aria-selected={false}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick("")}
                onMouseEnter={() => setHighlight(options.length)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left font-sans text-sm text-[#4a5058] dark:text-[#C3C2B7]",
                  highlight === options.length && "bg-[#e8fbf6] dark:bg-white/10",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">Clear selection</span>
                </span>
              </button>
            </li>
          )}
          {options.length === 0 && !hasClear && (
            <li className="px-3 py-2 font-sans text-sm text-[#8a8f98]">No options available.</li>
          )}
        </ul>
      )}
    </div>
  );
}

/** Multi-select combobox for agents-to-run — v2 field + dropdown list styling. */
function AgentsMultiSelect({
  agents,
  selected,
  onChange,
  disabled,
}: {
  agents: Agent[];
  selected: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
}): React.JSX.Element {
  const [open, setOpen] = React.useState(false);
  const [filter, setFilter] = React.useState("");
  const orderedNames = selected
    .map((id) => agents.find((a) => a.id === id)?.name)
    .filter((n): n is string => typeof n === "string" && n !== "");
  const q = filter.trim().toLowerCase();
  const visible = q ? agents.filter((a) => a.name.toLowerCase().includes(q)) : agents;

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  }

  const label =
    orderedNames.length === 0
      ? "Select agents…"
      : orderedNames.length === 1
        ? orderedNames[0]
        : `${orderedNames[0]} + ${orderedNames.length - 1} other${orderedNames.length - 1 === 1 ? "" : "s"}`;

  return (
    <div className={cn(disabled && "opacity-50")}>
      <div className="relative">
        <button
          type="button"
          onClick={() => {
            if (!disabled) setOpen((o) => !o);
          }}
          disabled={disabled}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="Agents to run"
          className={cn(
            "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-2 font-sans text-sm",
            "hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1",
            "dark:border-white/10 dark:bg-[#2e2e2e] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]",
            "disabled:cursor-not-allowed disabled:opacity-50",
          )}
        >
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-left",
              orderedNames.length > 0 ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98] dark:text-[#898781]",
            )}
          >
            {label}
          </span>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10">
            <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
          </span>
        </button>
        {open && (
          <>
            <button
              type="button"
              aria-hidden="true"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className="fixed inset-0 z-10 cursor-default bg-transparent"
            />
            <div
              role="listbox"
              aria-label="Agents to run"
              className="absolute inset-x-0 top-full z-20 mt-1 max-h-64 overflow-auto rounded-2xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] animate-[turtle-fade-in_120ms_ease-out] dark:border-white/10 dark:bg-[#1a1a1a]"
            >
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter…"
                aria-label="Filter agents"
                className="h-11 w-full min-w-0 rounded-xl border bg-white px-3 font-sans text-[#1d1d1d] placeholder:text-[#8a8f98] border-[#e5e7eb] hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1 dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC] dark:placeholder:text-[#898781] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]"
              />
              <div className="mt-2 grid gap-1">
                {visible.length === 0 ? (
                  <p className="px-2 py-1 font-sans text-xs text-[#8a8f98]">
                    {agents.length === 0 ? "No agents yet — create one on the Agents tab." : "No matches."}
                  </p>
                ) : (
                  visible.map((a) => (
                    <label
                      key={a.id}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 font-sans text-sm",
                        selected.includes(a.id)
                          ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                          : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                      )}
                    >
                      <input type="checkbox" checked={selected.includes(a.id)} onChange={() => toggle(a.id)} />
                      <span className="min-w-0 flex-1 truncate">{a.name}</span>
                      {selected.includes(a.id) && (
                        <Check className="h-4 w-4 shrink-0 text-[#0d5c4a] dark:text-[#2fdebf]" aria-hidden="true" />
                      )}
                    </label>
                  ))
                )}
              </div>
            </div>
          </>
        )}
      </div>
      {agents.length === 0 && (
        <p className="mt-1 font-sans text-sm font-normal normal-case tracking-normal text-[#b91c1c]">
          No agents yet — create one on the Agents tab.
        </p>
      )}
    </div>
  );
}

const SLOTS_KEY = "lab:last-model-slots";
const AUTO_KEY = "lab:auto-select";

function loadAutoSelect(): boolean {
  try {
    return localStorage.getItem(AUTO_KEY) === "1";
  } catch {
    return false;
  }
}

function isSlotLike(v: unknown): v is ModelSlot {
  if (!v || typeof v !== "object") return false;
  const s = v as Record<string, unknown>;
  return typeof s.model === "string" && typeof s.effort === "string";
}

function loadSlots(): ModelSlot[] {
  try {
    const raw = localStorage.getItem(SLOTS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isSlotLike).map((s) => ({ model: s.model, effort: s.effort }));
  } catch {
    return [];
  }
}

/** One reusable agent entry from POST /runs/check-existing. */
type ExistingAgent = {
  agent_id: string;
  agent_name: string;
  log_id: string;
  created_at: string;
  cost_usd: number | null;
};

type CheckExistingAgent = {
  agent_id?: unknown;
  agent_name?: unknown;
  log_id?: unknown;
  created_at?: unknown;
  cost_usd?: unknown;
  duration_ms?: unknown;
};

type CheckExistingSlot = {
  model?: unknown;
  reasoning_effort?: unknown;
  agents?: unknown;
};

type CheckExistingAutoSlot = {
  model?: unknown;
  reasoning_effort?: unknown;
  log?: unknown;
  agents?: unknown;
};

/** Total cost USD from an auto-run log (`usage.total_cost_usd` or equivalent). */
function autoCostUsd(log: LogRow): number | null {
  const usage = log.usage as unknown as
    | { total_cost_usd?: unknown; cost_usd?: unknown; total_cost?: unknown; cost?: unknown }
    | undefined;
  if (!usage || typeof usage !== "object") return null;
  const candidates = [usage.total_cost_usd, usage.cost_usd, usage.total_cost, usage.cost];
  for (const c of candidates) {
    if (typeof c === "number" && Number.isFinite(c)) return c;
  }
  return null;
}

function StatusBadge({ status }: { status: ColumnStatus }): React.JSX.Element {
  if (status === "done") return <Badge tone="success">done</Badge>;
  if (status === "partial") return <Badge tone="warning">partial</Badge>;
  if (status === "error") return <Badge tone="danger">error</Badge>;
  return <Badge tone="info">running</Badge>;
}

/**
 * Compare agents for the matrix: union of agents across finished columns
 * (auto-select runs may pick a different subset per model); before anything
 * finishes, from the selected agents with empty attributes so section
 * headers + skeletons show.
 */
function buildCompareAgents(
  columns: CompareColumn[],
  selectedIds: string[],
  allAgents: { id: string; name: string; kind: string }[],
): CompareAgent[] {
  const finishedLogs = columns
    .filter((c) => c.log && (c.status === "done" || c.status === "partial"))
    .map((c) => c.log as NonNullable<CompareColumn["log"]>);
  if (finishedLogs.length > 0) {
    const out = unionAgentsFromLogs(finishedLogs);
    if (out.length > 0) return out;
  }
  return selectedIds.map((id) => {
    const found = allAgents.find((a) => a.id === id);
    return {
      id,
      name: found?.name ?? id,
      kind: found?.kind ?? "extraction",
      attributes: [],
    };
  });
}

/** Live elapsed-seconds counter for a running column. */
function Elapsed({ startedAt }: { startedAt?: number }): React.JSX.Element | null {
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  if (!startedAt) return null;
  return <span>{Math.max(0, Math.round((now - startedAt) / 1000))}s</span>;
}

/** One per-model result block in picker order: header + SingleModelTable when it has a log. */
function ColumnBlock({
  column,
  onRetry,
  onRetryAgent,
  agentRunning,
}: {
  column: CompareColumn;
  onRetry: (key: string) => void;
  onRetryAgent?: (colKey: string, agentId: string) => void;
  agentRunning?: Record<string, boolean>;
}): React.JSX.Element {
  return (
    <div className="rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
      <div className="flex flex-wrap items-center gap-2">
        <CardTitle title={column.model}>{modelLabel(column.model, column.effort)}</CardTitle>
        <StatusBadge status={column.status} />
        {column.status === "running" && (
          <span className="inline-flex items-center gap-1.5 font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <Elapsed startedAt={column.startedAt} />
          </span>
        )}
        {column.log && (
          <span
            className="font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]"
            title="Total run cost (USD + INR)"
          >
            {fmtCostBoth(column.log.usage?.cost_usd)}
          </span>
        )}
        {column.status === "error" && (
          <Button variant="secondary" size="sm" onClick={() => onRetry(column.key)}>
            Retry
          </Button>
        )}
      </div>
      {column.status === "running" && !column.log && (
        <p className="mt-2 inline-flex items-center gap-2 font-sans text-sm text-[#4a5058] dark:text-[#C3C2B7]">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          Running…
        </p>
      )}
      {column.status === "error" && !column.log && column.error && (
        <p role="alert" className="mt-2 break-words font-sans text-sm text-[#b91c1c] dark:text-[#f87171]">
          {column.error}
        </p>
      )}
      {column.log && (
        <div className="mt-3">
          <SingleModelTable
            log={column.log}
            onRetryAgent={
              onRetryAgent ? (agentId) => onRetryAgent(column.key, agentId) : undefined
            }
            runningAgents={agentRunning}
            colKey={column.key}
          />
        </div>
      )}
    </div>
  );
}

export default function TestLab() {
  const [slots, setSlots] = React.useState<ModelSlot[]>(loadSlots);
  const [autoSelect, setAutoSelect] = React.useState<boolean>(loadAutoSelect);
  const [selected, setSelected] = React.useState<string[]>([]);
  const [clientId, setClientId] = React.useState("");
  const [selectedMeetingTypes, setSelectedMeetingTypes] = React.useState<string[]>([]);
  const [meetingDate, setMeetingDate] = React.useState("");
  const [meetingId, setMeetingId] = React.useState("");
  const [previewOpen, setPreviewOpen] = React.useState(false);
  const [checking, setChecking] = React.useState(false);
  const [pendingRun, setPendingRun] = React.useState<{
    slots: ModelSlot[];
    basePayload: Record<string, unknown>;
    reuse: Record<string, Record<string, string>>;
    details: Record<string, ExistingAgent[]>;
    selectedIds: string[];
  } | null>(null);
  const [pendingAutoRun, setPendingAutoRun] = React.useState<{
    slots: ModelSlot[];
    basePayload: Record<string, unknown>;
    reuseLogs: Record<string, string>;
    logs: Record<string, LogRow>;
  } | null>(null);
  const [pendingAutoAgents, setPendingAutoAgents] = React.useState<{
    slots: ModelSlot[];
    basePayload: Record<string, unknown>;
    reuse: Record<string, Record<string, string>>;
    details: Record<string, ExistingAgent[]>;
  } | null>(null);

  const multi = useMultiRun();

  React.useEffect(() => {
    try {
      localStorage.setItem(SLOTS_KEY, JSON.stringify(slots));
    } catch {
      // ignore persistence failures (private mode, quota)
    }
  }, [slots]);

  React.useEffect(() => {
    try {
      localStorage.setItem(AUTO_KEY, autoSelect ? "1" : "0");
    } catch {
      // ignore persistence failures (private mode, quota)
    }
  }, [autoSelect]);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as Agent[],
  });
  const enabledAgents = agents.filter((a) => a.is_enabled !== false);

  React.useEffect(() => {
    setSelected((s) => s.filter((id) => agents.some((a) => a.id === id && a.is_enabled !== false)));
  }, [agents]);

  const { data: modelsMeta } = useQuery({
    queryKey: ["models"],
    queryFn: async () => (await api.get("/models")).data as { live: boolean; models: ModelInfo[] },
    staleTime: 5 * 60 * 1000,
  });

  const clientsQuery = useQuery({
    queryKey: ["meeting-clients"],
    queryFn: async () => (await api.get("/meetings/clients")).data as MeetingClient[],
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const titlesQuery = useQuery({
    queryKey: ["meeting-titles", clientId],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (clientId) params.client_id = clientId;
      return (await api.get("/meetings/titles", { params })).data as { titles: string[] };
    },
    staleTime: 60 * 1000,
    retry: false,
  });

  const meetingsQuery = useQuery({
    // The type filter is applied client-side on the derived type (the
    // backend only exact-matches full titles) — never sent as `title`.
    queryKey: ["meetings", clientId, meetingDate],
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (clientId) params.client_id = clientId;
      if (meetingDate) params.date = meetingDate;
      return (await api.get("/meetings", { params })).data as { meetings: MeetingSummary[] };
    },
    staleTime: 30 * 1000,
    retry: false,
  });

  const transcriptQuery = useQuery({
    queryKey: ["meeting-transcript", meetingId],
    queryFn: async () =>
      (await api.get(`/meetings/${meetingId}/transcript`)).data as TranscriptResponse,
    enabled: meetingId !== "",
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const clientsDetail = clientsQuery.error ? serverDetail(clientsQuery.error) : null;
  const titlesDetail = titlesQuery.error ? serverDetail(titlesQuery.error) : null;
  const meetingsDetail = meetingsQuery.error ? serverDetail(meetingsQuery.error) : null;
  const pickerErrorDetail = clientsDetail ?? titlesDetail ?? meetingsDetail;

  React.useEffect(() => {
    if (clientsQuery.error) toast.error(`Meeting picker unavailable: ${serverDetail(clientsQuery.error)}`);
  }, [clientsQuery.error]);
  React.useEffect(() => {
    if (titlesQuery.error) toast.error(`Meeting titles unavailable: ${serverDetail(titlesQuery.error)}`);
  }, [titlesQuery.error]);
  React.useEffect(() => {
    if (meetingsQuery.error) toast.error(`Meetings unavailable: ${serverDetail(meetingsQuery.error)}`);
  }, [meetingsQuery.error]);
  React.useEffect(() => {
    if (transcriptQuery.error) toast.error(`Could not load transcript: ${serverDetail(transcriptQuery.error)}`);
  }, [transcriptQuery.error]);

  function handleClientChange(v: string) {
    setClientId(v);
    setSelectedMeetingTypes([]);
    setMeetingId("");
    setPreviewOpen(false);
  }

  function handleTypeChange(v: string[]) {
    setSelectedMeetingTypes(v);
    setMeetingId("");
    setPreviewOpen(false);
  }

  function handleDateChange(v: string) {
    setMeetingDate(v);
    setMeetingId("");
    setPreviewOpen(false);
  }

  function handleMeetingChange(v: string) {
    setMeetingId(v);
    setPreviewOpen(false);
  }

  // Same denormalized meeting snapshot the single-run payload built, minus
  // model / reasoning_effort (those come from each slot in useMultiRun).
  function buildBasePayload(): Record<string, unknown> {
    const clientName =
      (clientsQuery.data ?? []).find((c) => c.id === clientId)?.name ?? "";
    const picked = (meetingsQuery.data?.meetings ?? []).find((m) => m.id === meetingId);
    const instanceTitle = (picked?.title ?? transcriptQuery.data?.title ?? "").trim();
    return {
      input_type: "transcription",
      meeting_id: meetingId,
      agent_ids: selected,
      client: clientName,
      meeting_type: meetingTypeOf(instanceTitle) || selectedMeetingTypes[0] || "",
      meeting_title: instanceTitle,
    };
  }

  function handleRun() {
    const why = canRun();
    if (why) {
      toast.error(why);
      return;
    }
    const basePayload = buildBasePayload();
    const currentSlots = [...slots];
    const currentSelected = [...selected];
    if (autoSelect) {
      setChecking(true);
      api
        .post("/runs/check-existing-auto", {
          ...basePayload,
          models: currentSlots.map((s) => ({ model: s.model, reasoning_effort: s.effort })),
        })
        .then(
          (res) => {
            setChecking(false);
            const data = res.data as { slots?: unknown };
            const slotEntries = Array.isArray(data?.slots)
              ? (data.slots as CheckExistingAutoSlot[])
              : null;
            // Unknown shape: run fresh with no popup.
            if (!slotEntries) {
              multi.start(currentSlots, basePayload, { auto: true });
              return;
            }
            const validKeys = new Set(currentSlots.map((s) => `${s.model}|${s.effort}`));
            const logs: Record<string, LogRow> = {};
            const reuseLogs: Record<string, string> = {};
            const reuse: Record<string, Record<string, string>> = {};
            const details: Record<string, ExistingAgent[]> = {};
            let anyReusableAgents = false;
            for (const entry of slotEntries) {
              if (!entry || typeof entry.model !== "string") continue;
              const effort =
                typeof entry.reasoning_effort === "string" ? entry.reasoning_effort : "";
              const k = `${entry.model}|${effort}`;
              // Only keep entries for currently requested slots.
              if (!validKeys.has(k)) continue;
              if (!(k in logs)) {
                const log = entry.log;
                if (log && typeof log === "object" && !Array.isArray(log)) {
                  const id = (log as Record<string, unknown>).id;
                  if (typeof id === "string" && id !== "") {
                    logs[k] = log as LogRow;
                    reuseLogs[k] = id;
                  }
                }
              }
              // Always-run agents for this slot (same shape as check-existing).
              const rawAgents = Array.isArray(entry.agents) ? entry.agents : [];
              const clean: ExistingAgent[] = [];
              for (const raw of rawAgents as CheckExistingAgent[]) {
                if (!raw || typeof raw.agent_id !== "string" || typeof raw.log_id !== "string") {
                  continue;
                }
                if (raw.log_id === "") continue;
                clean.push({
                  agent_id: raw.agent_id,
                  agent_name:
                    typeof raw.agent_name === "string" && raw.agent_name.trim() !== ""
                      ? raw.agent_name
                      : raw.agent_id,
                  log_id: raw.log_id,
                  created_at: typeof raw.created_at === "string" ? raw.created_at : "",
                  cost_usd: typeof raw.cost_usd === "number" ? raw.cost_usd : null,
                });
              }
              details[k] = clean;
              if (clean.length > 0) {
                anyReusableAgents = true;
                const map: Record<string, string> = {};
                for (const c of clean) map[c.agent_id] = c.log_id;
                reuse[k] = map;
              }
            }
            if (Object.keys(logs).length > 0) {
              setPendingAutoRun({ slots: currentSlots, basePayload, reuseLogs, logs });
              return;
            }
            // Whole auto run does not exist, but some always-run agents do.
            for (const s of currentSlots) {
              const k = `${s.model}|${s.effort}`;
              if (!(k in details)) details[k] = [];
            }
            if (anyReusableAgents) {
              setPendingAutoAgents({ slots: currentSlots, basePayload, reuse, details });
              return;
            }
            multi.start(currentSlots, basePayload, { auto: true });
          },
          () => {
            // Any error (including 404 on old backends): run as today.
            setChecking(false);
            multi.start(currentSlots, basePayload, { auto: true });
          },
        );
      return;
    }
    setChecking(true);
    api
      .post("/runs/check-existing", {
        ...basePayload,
        models: currentSlots.map((s) => ({ model: s.model, reasoning_effort: s.effort })),
      })
      .then(
        (res) => {
          setChecking(false);
          const data = res.data as { slots?: CheckExistingSlot[] };
          const slotEntries = Array.isArray(data?.slots) ? data.slots : null;
          // Unknown shape: run normally with no popup.
          if (!slotEntries) {
            multi.start(currentSlots, basePayload);
            return;
          }
          const reuse: Record<string, Record<string, string>> = {};
          const details: Record<string, ExistingAgent[]> = {};
          let anyReusable = false;
          for (const entry of slotEntries) {
            if (!entry || typeof entry.model !== "string") continue;
            const effort =
              typeof entry.reasoning_effort === "string" ? entry.reasoning_effort : "";
            const k = `${entry.model}|${effort}`;
            // Only keep entries for currently requested slots.
            if (!currentSlots.some((s) => `${s.model}|${s.effort}` === k)) continue;
            const rawAgents = Array.isArray(entry.agents) ? entry.agents : [];
            const clean: ExistingAgent[] = [];
            for (const raw of rawAgents as CheckExistingAgent[]) {
              if (!raw || typeof raw.agent_id !== "string" || typeof raw.log_id !== "string") {
                continue;
              }
              if (raw.log_id === "" || !currentSelected.includes(raw.agent_id)) continue;
              clean.push({
                agent_id: raw.agent_id,
                agent_name:
                  typeof raw.agent_name === "string" && raw.agent_name.trim() !== ""
                    ? raw.agent_name
                    : raw.agent_id,
                log_id: raw.log_id,
                created_at: typeof raw.created_at === "string" ? raw.created_at : "",
                cost_usd: typeof raw.cost_usd === "number" ? raw.cost_usd : null,
              });
            }
            details[k] = clean;
            if (clean.length > 0) {
              anyReusable = true;
              const map: Record<string, string> = {};
              for (const c of clean) map[c.agent_id] = c.log_id;
              reuse[k] = map;
            }
          }
          // Every requested slot gets a details entry so the modal can show
          // "will run fresh" for slots with no reusable output.
          for (const s of currentSlots) {
            const k = `${s.model}|${s.effort}`;
            if (!(k in details)) details[k] = [];
          }
          if (!anyReusable) {
            multi.start(currentSlots, basePayload);
            return;
          }
          setPendingRun({
            slots: currentSlots,
            basePayload,
            reuse,
            details,
            selectedIds: currentSelected,
          });
        },
        () => {
          // Any error (including 404 on old backends): run as today.
          setChecking(false);
          multi.start(currentSlots, basePayload);
        },
      );
  }

  function canRun() {
    if (!meetingId) return "Select a meeting first";
    if (transcriptQuery.isFetching || transcriptQuery.isLoading) return "Transcript still loading…";
    if (!transcriptQuery.data?.transcription?.trim()) return "Transcript still loading…";
    if (slots.length === 0) return "Add a model first";
    if (!autoSelect && selected.length === 0) return "Select at least one agent";
    return null;
  }

  const clientOptions: FilterOption[] = (clientsQuery.data ?? []).map((c) => ({
    value: c.id,
    label: c.name,
  }));
  // Distinct derived meeting types from the available full titles (the
  // title list is scoped by client, so these narrow with it); "" skipped.
  const typeOptions: FilterOption[] = React.useMemo(() => {
    const set = new Set<string>();
    for (const t of titlesQuery.data?.titles ?? []) {
      const v = meetingTypeOf(t);
      if (v) set.add(v);
    }
    return [...set]
      .sort((a, b) => a.localeCompare(b))
      .map((v) => ({ value: v, label: v }));
  }, [titlesQuery.data]);
  // Type selection narrows the instance list client-side on the derived type.
  const visibleMeetings: MeetingSummary[] = React.useMemo(() => {
    const all = meetingsQuery.data?.meetings ?? [];
    if (selectedMeetingTypes.length === 0) return all;
    const want = new Set(selectedMeetingTypes);
    return all.filter((m) => want.has(meetingTypeOf(m.title)));
  }, [meetingsQuery.data, selectedMeetingTypes]);
  const meetingOptions: FilterOption[] = visibleMeetings.map((m) => {
    const parts: string[] = [];
    if (typeof m.duration_min === "number") parts.push(`${m.duration_min} min`);
    if (m.participants && m.participants.length > 0) parts.push(m.participants.join(", "));
    return {
      value: m.id,
      label: `${m.title} — ${formatMeetingDate(m.date)}`,
      sub: parts.length > 0 ? parts.join(" · ") : undefined,
    };
  });

  const clientDisabled = clientsQuery.isLoading || clientsDetail !== null;
  const dateDisabled = false;
  const meetingDisabled = meetingsQuery.isLoading || meetingsDetail !== null;

  const compareAgents = React.useMemo(
    () => buildCompareAgents(multi.columns, selected, agents),
    [multi.columns, selected, agents],
  );

  // Cost-hint inputs: the transcript text the run will send (omit the hint
  // when no text is loaded) + each selected agent's system prompt preview.
  const transcriptChars = transcriptQuery.data?.transcription?.length ?? 0;
  const previewQueries = useQueries({
    queries: selected.map((id) => ({
      queryKey: ["prompt-preview", id],
      queryFn: async () =>
        (await api.get(`/agents/${id}/prompt-preview`)).data as {
          system?: unknown;
          attributes?: unknown;
        },
      staleTime: 5 * 60 * 1000,
      retry: false,
    })),
  });
  const previewPending = previewQueries.some((q) => q.isLoading || q.isFetching);
  const previewFailed = previewQueries.some((q) => q.isError);
  const previewData = previewQueries.map((q) => q.data);
  const estimates = React.useMemo((): Record<string, { inTokens: number; outTokens: number; usd: number | null }> | undefined => {
    if (transcriptChars === 0 || selected.length === 0 || slots.length === 0) return undefined;
    if (previewPending || previewFailed) return undefined;
    if (previewData.some((d) => !d)) return undefined;
    const agentPrompts = previewData.map((d) => {
      const rec = (d ?? {}) as { system?: unknown; attributes?: unknown };
      return {
        systemChars: typeof rec.system === "string" ? rec.system.length : 0,
        attrCount: Array.isArray(rec.attributes) ? rec.attributes.length : 0,
      };
    });
    const out: Record<string, { inTokens: number; outTokens: number; usd: number | null }> = {};
    for (const s of slots) {
      const pricing =
        (modelsMeta?.models ?? []).find((m) => m.id === s.model)?.pricing ?? null;
      out[`${s.model}|${s.effort}`] = estimateRunCost({ transcriptChars, agentPrompts, pricing });
    }
    return out;
  }, [transcriptChars, selected, slots, modelsMeta, previewPending, previewFailed, previewData]);

  return (
    <div className="grid gap-4">
      <Card>
        <div className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <span className={fieldLabel}>Input type</span>
              <input
                value="transcription"
                disabled
                aria-label="Input type (locked to transcription)"
                className={cn(fieldInput, "h-11 opacity-70")}
              />
            </div>
            <div className="sm:col-span-2">
              <div className="flex flex-wrap items-center gap-2">
                <KeyStatusBadge />
              </div>
              <ModelSlotsPicker
                slots={slots}
                onChange={setSlots}
                models={modelsMeta?.models ?? []}
                estimates={estimates}
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <SingleSelectFilter
                value={clientId}
                onChange={handleClientChange}
                options={clientOptions}
                placeholder={
                  clientsDetail !== null
                    ? "Unavailable — check backend"
                    : clientsQuery.isLoading
                      ? "Loading clients…"
                      : "Select client… (optional)"
                }
                disabled={clientDisabled}
                ariaLabel="Client"
                filterPlaceholder="Search clients…"
                emptyText={clientOptions.length === 0 ? "No clients yet." : "No matches."}
              />
            </div>
            <div>
              <MultiSelectFilter
                options={typeOptions}
                selected={selectedMeetingTypes}
                onChange={handleTypeChange}
                placeholder={
                  titlesDetail !== null
                    ? "Unavailable — check backend"
                    : titlesQuery.isLoading
                      ? "Loading types…"
                      : "All meeting types"
                }
                ariaLabel="Meeting type"
                filterPlaceholder="Search meeting types…"
                emptyText={typeOptions.length === 0 ? "No meeting types yet." : "No matches."}
              />
            </div>
            <div className="flex items-end">
              <input
                type="date"
                value={meetingDate}
                onChange={(e) => handleDateChange(e.target.value)}
                disabled={dateDisabled}
                aria-label="Meeting start date"
                className={cn(fieldInput, "h-11 disabled:cursor-not-allowed disabled:opacity-50")}
              />
            </div>
          </div>
          {pickerErrorDetail && (
            <p
              role="alert"
              className="rounded-xl border border-[#f59e0b]/40 bg-[#fffbeb] p-3 font-sans text-sm text-[#92400e] dark:border-[#f59e0b]/30 dark:bg-[#f59e0b]/10 dark:text-[#fcd34d]"
            >
              Meeting picker unavailable: {pickerErrorDetail}
            </p>
          )}
          <div>
            <FilterCombobox
              value={meetingId}
              onChange={handleMeetingChange}
              options={meetingOptions}
              placeholder={
                meetingsDetail !== null
                  ? "Unavailable — check backend"
                  : meetingsQuery.isLoading
                    ? "Loading meetings…"
                    : "Select a meeting…"
              }
              disabled={meetingDisabled}
              ariaLabel="Meeting"
            />
          </div>
          <div className={fieldLabel}>
            <div className="mt-1.5 grid gap-2">
              <label className="flex cursor-pointer items-center gap-2 font-sans text-sm font-normal normal-case tracking-normal text-[#1d1d1d] dark:text-[#F0EFEC]">
                <input
                  type="checkbox"
                  checked={autoSelect}
                  onChange={(e) => setAutoSelect(e.target.checked)}
                  aria-label="Auto Select Agents"
                />
                <span>Auto Select Agents</span>
              </label>
              <AgentsMultiSelect
                agents={enabledAgents}
                selected={selected}
                onChange={setSelected}
                disabled={autoSelect}
              />
              {autoSelect && (
                <p className="font-sans text-xs font-normal normal-case tracking-normal text-[#4a5058] dark:text-[#C3C2B7]">
                  Agent identifier picks agents per model
                </p>
              )}
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              loading={transcriptQuery.isFetching}
              disabled={!transcriptQuery.data?.transcription?.trim()}
              onClick={() => setPreviewOpen(true)}
              aria-label="Preview scrubbed transcription"
            >
              <Eye className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button loading={multi.running || checking} onClick={handleRun} disabled={checking}>
              {checking ? (
                "Checking…"
              ) : (
                <>
                  <Play className="h-4 w-4" aria-hidden="true" />{" "}
                  {slots.length > 1 ? `Run ${slots.length} models` : "Run"}
                </>
              )}
            </Button>
          </div>
        </div>
      </Card>

      {previewOpen && (
        <Modal
          title={
            transcriptQuery.data
              ? `${transcriptQuery.data.title} — ${formatMeetingDate(transcriptQuery.data.date)}`
              : "Scrubbed transcription"
          }
          onClose={() => setPreviewOpen(false)}
          wide
        >
          {transcriptQuery.isFetching || transcriptQuery.isLoading ? (
            <p className="font-sans text-sm text-[#4a5058] dark:text-[#C3C2B7]">Loading transcription…</p>
          ) : transcriptQuery.error ? (
            <p role="alert" className="font-sans text-sm text-[#b91c1c] dark:text-[#f87171]">
              Could not load transcript: {serverDetail(transcriptQuery.error)}
            </p>
          ) : (
            <div className="grid gap-3">
              <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-xl border border-[#e5e7eb] bg-[#f1f2f3] p-4 font-sans text-sm text-[#1d1d1d] dark:border-white/10 dark:bg-white/5 dark:text-[#F0EFEC]">
                {transcriptQuery.data?.transcription ?? ""}
              </pre>
              <p className="font-sans text-xs text-[#8a8f98]">
                PII scrubbed automatically — only this text reaches the model.
              </p>
            </div>
          )}
        </Modal>
      )}

      {pendingRun && (
        <Modal title="Output already exists" onClose={() => setPendingRun(null)}>
          <div className="grid gap-3">
            <ul className="grid gap-2">
              {pendingRun.slots.map((s) => {
                const k = `${s.model}|${s.effort}`;
                const reusable = pendingRun.details[k] ?? [];
                const freshIds = pendingRun.selectedIds.filter(
                  (id) => !reusable.some((r) => r.agent_id === id),
                );
                const freshNames = freshIds.map(
                  (id) => agents.find((a) => a.id === id)?.name ?? id,
                );
                if (reusable.length === 0) {
                  return (
                    <li
                      key={k}
                      className="grid gap-1 rounded-xl border border-dashed border-[#e5e7eb] px-3 py-2 dark:border-white/10"
                    >
                      <span
                        className="min-w-0 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                        title={s.model}
                      >
                        {modelLabel(s.model, s.effort)}
                      </span>
                      <span className="font-sans text-xs text-[#8a8f98]">will run fresh</span>
                    </li>
                  );
                }
                return (
                  <li
                    key={k}
                    className="grid gap-1.5 rounded-xl border border-[#e5e7eb] bg-[#f1f2f3]/60 px-3 py-2 dark:border-white/10 dark:bg-white/5"
                  >
                    <span
                      className="min-w-0 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                      title={s.model}
                    >
                      {modelLabel(s.model, s.effort)}
                    </span>
                    <ul className="grid gap-1">
                      {reusable.map((r) => (
                        <li
                          key={r.agent_id}
                          className="flex items-baseline justify-between gap-3 font-sans text-xs"
                        >
                          <span className="min-w-0 flex-1 truncate text-[#1d1d1d] dark:text-[#F0EFEC]">
                            {r.agent_name}
                          </span>
                          <span
                            className="shrink-0 text-[#4a5058] dark:text-[#C3C2B7]"
                            title={r.created_at || undefined}
                          >
                            {r.created_at ? fmtRelative(r.created_at) : "previous run"}
                            {" · "}
                            {fmtCostBoth(r.cost_usd)}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {freshNames.length > 0 && (
                      <span className="font-sans text-xs text-[#8a8f98]">
                        will run fresh: {freshNames.join(", ")}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => setPendingRun(null)}>
                Cancel
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  const p = pendingRun;
                  setPendingRun(null);
                  multi.start(p.slots, p.basePayload);
                }}
              >
                Regenerate all
              </Button>
              <Button
                autoFocus
                onClick={() => {
                  const p = pendingRun;
                  setPendingRun(null);
                  multi.start(p.slots, p.basePayload, { reuse: p.reuse });
                }}
              >
                Reuse existing, run the rest
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {pendingAutoRun && (
        <Modal title="Auto run already exists" onClose={() => setPendingAutoRun(null)}>
          <div className="grid gap-3">
            <p className="font-sans text-sm text-[#4a5058] dark:text-[#C3C2B7]">
              These models were already auto-run on this input with the same identifier prompt.
            </p>
            <ul className="grid gap-2">
              {pendingAutoRun.slots.map((s) => {
                const k = `${s.model}|${s.effort}`;
                const log = pendingAutoRun.logs[k];
                if (!log) {
                  return (
                    <li
                      key={k}
                      className="grid gap-1 rounded-xl border border-dashed border-[#e5e7eb] px-3 py-2 dark:border-white/10"
                    >
                      <span
                        className="min-w-0 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                        title={s.model}
                      >
                        {modelLabel(s.model, s.effort)}
                      </span>
                      <span className="font-sans text-xs text-[#8a8f98]">will run fresh</span>
                    </li>
                  );
                }
                const cost = autoCostUsd(log);
                const createdAt =
                  typeof log.created_at === "string" ? log.created_at : "";
                return (
                  <li
                    key={k}
                    className="grid gap-1 rounded-xl border border-[#e5e7eb] bg-[#f1f2f3]/60 px-3 py-2 dark:border-white/10 dark:bg-white/5"
                  >
                    <span
                      className="min-w-0 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                      title={s.model}
                    >
                      {modelLabel(s.model, s.effort)}
                    </span>
                    <span
                      className="font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]"
                      title={createdAt || undefined}
                    >
                      {createdAt ? fmtRelative(createdAt) : "previous run"}
                      {cost !== null ? ` · ${fmtCostBoth(cost)}` : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => setPendingAutoRun(null)}>
                Cancel
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  const p = pendingAutoRun;
                  setPendingAutoRun(null);
                  multi.start(p.slots, p.basePayload, { auto: true });
                }}
              >
                Regenerate
              </Button>
              <Button
                autoFocus
                onClick={() => {
                  const p = pendingAutoRun;
                  setPendingAutoRun(null);
                  multi.start(p.slots, p.basePayload, { auto: true, reuseLogs: p.reuseLogs });
                }}
              >
                Show last output
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {pendingAutoAgents && (
        <Modal title="Output already exists" onClose={() => setPendingAutoAgents(null)}>
          <div className="grid gap-3">
            <ul className="grid gap-2">
              {pendingAutoAgents.slots.map((s) => {
                const k = `${s.model}|${s.effort}`;
                const reusable = pendingAutoAgents.details[k] ?? [];
                if (reusable.length === 0) {
                  return (
                    <li
                      key={k}
                      className="grid gap-1 rounded-xl border border-dashed border-[#e5e7eb] px-3 py-2 dark:border-white/10"
                    >
                      <span
                        className="min-w-0 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                        title={s.model}
                      >
                        {modelLabel(s.model, s.effort)}
                      </span>
                      <span className="font-sans text-xs text-[#8a8f98]">will run fresh</span>
                    </li>
                  );
                }
                return (
                  <li
                    key={k}
                    className="grid gap-1.5 rounded-xl border border-[#e5e7eb] bg-[#f1f2f3]/60 px-3 py-2 dark:border-white/10 dark:bg-white/5"
                  >
                    <span
                      className="min-w-0 truncate font-mono text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                      title={s.model}
                    >
                      {modelLabel(s.model, s.effort)}
                    </span>
                    <span className="font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                      These agents were already extracted for {modelLabel(s.model, s.effort)}
                    </span>
                    <ul className="grid gap-1">
                      {reusable.map((r) => (
                        <li
                          key={r.agent_id}
                          className="flex items-baseline justify-between gap-3 font-sans text-xs"
                        >
                          <span className="min-w-0 flex-1 truncate text-[#1d1d1d] dark:text-[#F0EFEC]">
                            {r.agent_name}
                          </span>
                          <span
                            className="shrink-0 text-[#4a5058] dark:text-[#C3C2B7]"
                            title={r.created_at || undefined}
                          >
                            {r.created_at ? fmtRelative(r.created_at) : "previous run"}
                            {" · "}
                            {fmtCostBoth(r.cost_usd)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </li>
                );
              })}
            </ul>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => setPendingAutoAgents(null)}>
                Cancel
              </Button>
              <Button
                variant="secondary"
                onClick={() => {
                  const p = pendingAutoAgents;
                  setPendingAutoAgents(null);
                  multi.start(p.slots, p.basePayload, { auto: true });
                }}
              >
                Regenerate
              </Button>
              <Button
                autoFocus
                onClick={() => {
                  const p = pendingAutoAgents;
                  setPendingAutoAgents(null);
                  multi.start(p.slots, p.basePayload, { auto: true, reuse: p.reuse });
                }}
              >
                Reuse
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {multi.columns.length > 0 && (
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <CardTitle>Results</CardTitle>
            {multi.groupId && <Badge tone="neutral">group {multi.groupId.slice(0, 8)}</Badge>}
            <Link
              to="/logs"
              className="ml-auto font-heading text-xs font-bold text-[#0d5c4a] hover:underline dark:text-[#2fdebf]"
            >
              Open in Logs
            </Link>
          </div>
          {multi.columns.length > 1 ? (
            <ComparisonMatrix
              columns={multi.columns}
              agents={compareAgents}
              editable
              onRetry={multi.retry}
              onRetryAgent={multi.retryAgent}
              agentRunning={multi.agentRunning}
            />
          ) : (
            <div className="grid gap-3">
              {multi.columns.map((column) => (
                <ColumnBlock
                  key={column.key}
                  column={column}
                  onRetry={multi.retry}
                  onRetryAgent={multi.retryAgent}
                  agentRunning={multi.agentRunning}
                />
              ))}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
