import * as React from "react";
import { useState } from "react";
import { BarChart3, Braces, ChevronDown, ChevronLeft, ChevronRight, Copy, Sparkles } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, meetingTypeOf } from "../lib/api";
import type { LogsQueryParams } from "../lib/api";
import { fmt, fmtCostBoth, fmtMs, fmtTokens } from "../lib/format";
import type { LogFilters, LogRow, RunUsage } from "../lib/logTypes";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { MultiSelectFilter } from "../components/ui/Combobox";
import type { ModelOption } from "../components/ui/Combobox";
import { SingleModelTable } from "../components/compare/SingleModelTable";

type DrawerTab = "pretty" | "raw" | "analytics";

type LogAgent = { id: string; name: string };

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** YYYY-MM-DD of a log timestamp in Asia/Kolkata (matches the displayed date). */
function toLogDate(ts: string): string | null {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return null;
  try {
    const s = d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
    return DATE_RE.test(s) ? s : null;
  } catch {
    return null;
  }
}

/** Distinct non-blank values for one denormalized meeting field ("": unknown, skipped). */
function distinctLogOptions(logs: LogRow[], pick: (l: LogRow) => string | undefined) {
  const set = new Set<string>();
  for (const l of logs) {
    const v = (pick(l) ?? "").trim();
    if (v) set.add(v);
  }
  return [...set]
    .sort((a, b) => a.localeCompare(b))
    .map((v) => ({ value: v, label: v }));
}

/**
 * Derived Meeting Type for a log row: the helper over the full snapshot
 * title, falling back to the stored meeting_type when the title is blank
 * (old rows). "" means unknown.
 */
function logMeetingType(l: LogRow): string {
  return meetingTypeOf(l.meeting_title) || meetingTypeOf(l.meeting_type);
}

/** Empty/missing usage (old rows) degrades to "—" everywhere. */
function getUsage(l: LogRow): RunUsage | null {
  const u = l.usage;
  if (!u || typeof u !== "object") return null;
  if (typeof u.total_tokens !== "number") return null;
  return u;
}

function filterChips(f: LogFilters | undefined): string[] {
  if (!f || typeof f !== "object") return [];
  const chips: string[] = [];
  const client = f.client_name ?? f.client_id;
  if (client) chips.push(client);
  if (f.meeting_title) chips.push(f.meeting_title);
  if (f.meeting_date) chips.push(f.meeting_date);
  return chips;
}

function ratingsCount(l: LogRow): number {
  return Object.values(l.feedback ?? {}).reduce((n, m) => n + Object.keys(m ?? {}).length, 0);
}

const sectionLabel = "font-heading text-xs font-bold uppercase tracking-wide text-[#4a5058] dark:text-[#C3C2B7]";
const codeBlock =
  "mt-1.5 max-h-60 max-w-full overflow-auto whitespace-pre-wrap break-all rounded-xl bg-[#f1f2f3] p-3 font-mono text-xs text-[#1d1d1d] dark:bg-white/5 dark:text-[#F0EFEC]";

const TABS: { id: DrawerTab; label: string; icon: typeof Sparkles }[] = [
  { id: "pretty", label: "Pretty", icon: Sparkles },
  { id: "raw", label: "Raw", icon: Braces },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
];

const INPUT_FILTERS = ["transcription", "messages", "mail"];

export default function Logs() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tab, setTab] = useState<DrawerTab>("pretty");
  const [selectedInputTypes, setSelectedInputTypes] = useState<string[]>([]);
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [selectedAgents, setSelectedAgents] = useState<string[]>([]);
  const [selectedDates, setSelectedDates] = useState<string[]>([]);
  const [selectedClients, setSelectedClients] = useState<string[]>([]);
  const [selectedMeetingTypes, setSelectedMeetingTypes] = useState<string[]>([]);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as LogAgent[],
    staleTime: 60 * 1000,
  });
  const { data: modelsMeta } = useQuery({
    queryKey: ["models"],
    queryFn: async () =>
      (await api.get("/models")).data as { live: boolean; models: ModelOption[] },
    staleTime: 5 * 60 * 1000,
  });
  // Unfiltered list: stable source for the Model/Date dropdown options.
  const { data: allLogs = [], isLoading: allLoading } = useQuery({
    queryKey: ["logs-all"],
    queryFn: async () => (await api.get("/logs")).data as LogRow[],
    staleTime: 30 * 1000,
  });

  const hasServerFilters =
    selectedModels.length > 0 || selectedAgents.length > 0 || selectedDates.length > 0 ||
    selectedClients.length > 0;

  const params: LogsQueryParams = {};
  if (selectedModels.length > 0) params.models = selectedModels;
  if (selectedAgents.length > 0) params.agent_ids = selectedAgents;
  if (selectedDates.length > 0) params.dates = selectedDates;
  if (selectedClients.length > 0) params.clients = selectedClients;

  // Server-filtered list (AND across groups); reused only while filters are set.
  // Meeting-type filtering stays client-side on the derived type (no backend change).
  const { data: serverLogs, isLoading: serverLoading } = useQuery({
    queryKey: ["logs", selectedModels, selectedAgents, selectedDates,
      selectedClients],
    queryFn: async () =>
      (await api.get("/logs", { params, paramsSerializer: { indexes: null } })).data as LogRow[],
    enabled: hasServerFilters,
    staleTime: 30 * 1000,
  });

  const base = hasServerFilters ? (serverLogs ?? []) : allLogs;
  const isLoading = allLoading || (hasServerFilters && serverLoading);
  const totalCount = allLogs.length;

  const modelOptions = React.useMemo(() => {
    const names = new Map<string, string>();
    for (const m of modelsMeta?.models ?? []) {
      if (typeof m.id === "string" && m.id !== "") {
        names.set(m.id, typeof m.name === "string" && m.name !== "" ? m.name : m.id);
      }
    }
    for (const l of allLogs) {
      if (typeof l.model === "string" && l.model.trim() !== "" && !names.has(l.model)) {
        names.set(l.model, l.model);
      }
    }
    return [...names.entries()]
      .map(([value, name]) => ({
        value,
        label: value,
        sub: name !== value ? name : undefined,
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [allLogs, modelsMeta]);

  const agentOptions = React.useMemo(
    () =>
      [...agents]
        .filter((a) => a && typeof a.id === "string" && typeof a.name === "string")
        .map((a) => ({ value: a.id, label: a.name }))
        .sort((a, b) => a.label.localeCompare(b.label)),
    [agents],
  );

  const dateOptions = React.useMemo(() => {
    const set = new Set<string>();
    for (const l of allLogs) {
      const d = toLogDate(l.created_at);
      if (d) set.add(d);
    }
    return [...set].sort().reverse().map((d) => ({ value: d, label: d }));
  }, [allLogs]);

  // Meeting options: distinct non-blank snapshot values from the unfiltered
  // list (stable while filtering, same as Date options); "" means unknown.
  const clientOptions = React.useMemo(
    () => distinctLogOptions(allLogs, (l) => l.client),
    [allLogs],
  );
  // Distinct non-blank derived meeting types from the unfiltered list
  // (stable while filtering, same as Date options); "" means unknown, skipped.
  const meetingTypeOptions = React.useMemo(
    () => distinctLogOptions(allLogs, logMeetingType),
    [allLogs],
  );
  // Static input-type options (stable, same shape as the other filters).
  const inputTypeOptions = React.useMemo(
    () => INPUT_FILTERS.map((v) => ({ value: v, label: v })),
    [],
  );

  const active = activeId ? (allLogs.find((l) => l.id === activeId) ?? null) : null;

  function openLog(id: string) {
    setTab("pretty");
    setActiveId(id);
  }

  const typeFiltered =
    selectedMeetingTypes.length > 0
      ? base.filter((l) => selectedMeetingTypes.includes(logMeetingType(l)))
      : base;
  const filtered =
    selectedInputTypes.length > 0
      ? typeFiltered.filter((l) => selectedInputTypes.includes(l.input_type))
      : typeFiltered;

  return (
    <div className="grid gap-4">
      {active ? (
        <LogDetail log={active} tab={tab} onTab={setTab} onBack={() => setActiveId(null)} />
      ) : (
        <>
          <Card>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div>
                <MultiSelectFilter
                  options={modelOptions}
                  selected={selectedModels}
                  onChange={setSelectedModels}
                  placeholder="All models"
                  ariaLabel="Filter by model"
                  filterPlaceholder="Search models…"
                  emptyText={modelOptions.length === 0 ? "No models yet." : "No matches."}
                />
              </div>
              <div>
                <MultiSelectFilter
                  options={agentOptions}
                  selected={selectedAgents}
                  onChange={setSelectedAgents}
                  placeholder="All agents"
                  ariaLabel="Filter by agent"
                  filterPlaceholder="Search agents…"
                  emptyText={agents.length === 0 ? "No agents yet." : "No matches."}
                />
              </div>
              <div>
                <MultiSelectFilter
                  options={dateOptions}
                  selected={selectedDates}
                  onChange={setSelectedDates}
                  placeholder="All dates"
                  ariaLabel="Filter by date"
                  filterPlaceholder="Search dates…"
                  emptyText={dateOptions.length === 0 ? "No dates yet." : "No matches."}
                />
              </div>
              <div>
                <MultiSelectFilter
                  options={clientOptions}
                  selected={selectedClients}
                  onChange={setSelectedClients}
                  placeholder="All clients"
                  ariaLabel="Filter by client"
                  filterPlaceholder="Search clients…"
                  emptyText={clientOptions.length === 0 ? "No clients yet." : "No matches."}
                />
              </div>
              <div>
                <MultiSelectFilter
                  options={meetingTypeOptions}
                  selected={selectedMeetingTypes}
                  onChange={setSelectedMeetingTypes}
                  placeholder="All meeting types"
                  ariaLabel="Filter by meeting type"
                  filterPlaceholder="Search meeting types…"
                  emptyText={meetingTypeOptions.length === 0 ? "No meeting types yet." : "No matches."}
                />
              </div>
              <div>
                <MultiSelectFilter
                  options={inputTypeOptions}
                  selected={selectedInputTypes}
                  onChange={setSelectedInputTypes}
                  placeholder="All input types"
                  ariaLabel="Filter by input type"
                  filterPlaceholder="Search input types…"
                  emptyText="No matches."
                />
              </div>
            </div>
          </Card>

          {isLoading ? (
            <Card>
              <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
            </Card>
          ) : totalCount === 0 ? (
            <Card>
              <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
                No runs logged yet — run something from the Test Lab.
              </p>
            </Card>
          ) : filtered.length === 0 ? (
            <Card>
              <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
                No logs match these filters.
              </p>
            </Card>
          ) : (
            <Card padded={false} className="overflow-x-auto">
              <table className="w-full min-w-[920px] text-left text-sm">
                <thead>
                  <tr className="border-b border-[#e5e7eb] font-heading text-xs font-bold uppercase tracking-wide text-[#8a8f98] dark:border-white/10">
                    <th className="px-4 py-3">Date</th>
                    <th className="px-4 py-3">Model</th>
                    <th className="px-4 py-3">Effort</th>
                    <th className="px-4 py-3">Input</th>
                    <th className="px-4 py-3">Agents</th>
                    <th className="px-4 py-3">Ratings</th>
                    <th className="px-4 py-3">Cost</th>
                    <th className="px-4 py-3">Tokens</th>
                    <th className="px-4 py-3">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((l) => {
                    const fbCount = ratingsCount(l);
                    const usage = getUsage(l);
                    return (
                      <tr
                        key={l.id}
                        onClick={() => openLog(l.id)}
                        className="cursor-pointer border-b border-[#e5e7eb] last:border-0 hover:bg-[#e8fbf6]/50 dark:border-white/10 dark:hover:bg-white/5"
                      >
                        <td className="whitespace-nowrap px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {fmt(l.created_at)}
                        </td>
                        <td className="max-w-48 truncate px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]" title={l.model}>
                          {l.model}
                        </td>
                        <td className="max-w-48 truncate px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]" title={(l.reasoning_effort ?? "").trim() || "—"}>
                          {(l.reasoning_effort ?? "").trim() || "—"}
                        </td>
                        <td className="px-4 py-3">
                          <Badge tone="brand">{l.input_type}</Badge>
                        </td>
                        <td className="px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {Object.keys(l.agent_snapshot ?? {}).length}
                        </td>
                        <td className="px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {fbCount}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {usage ? fmtCostBoth(usage.cost_usd) : "—"}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {usage ? fmtTokens(usage.total_tokens) : "—"}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {usage ? fmtMs(usage.duration_ms) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function LogDetail({
  log,
  tab,
  onTab,
  onBack,
}: {
  log: LogRow;
  tab: DrawerTab;
  onTab: (t: DrawerTab) => void;
  onBack: () => void;
}) {
  const fbCount = ratingsCount(log);
  const usage = getUsage(log);
  const chips = filterChips(log.filters);

  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" size="sm" onClick={onBack} aria-label="Back to logs">
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          Back to logs
        </Button>
      </div>
      <div
        role="tablist"
        aria-label="Log detail views"
        className="flex items-center gap-1 rounded-full border border-[#e5e7eb] bg-[#f1f2f3] p-1 dark:border-white/10 dark:bg-white/5"
      >
        {TABS.map((t) => {
          const Icon = t.icon;
          const selected = tab === t.id;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={selected}
              onClick={() => onTab(t.id)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 rounded-full px-4 py-2 font-heading text-xs font-bold transition-colors",
                selected
                  ? "bg-white text-[#1d1d1d] shadow-[0_1px_4px_rgba(29,29,29,0.12)] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
                  : "text-[#4a5058] hover:text-[#1d1d1d] dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]",
              )}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === "pretty" && <PrettyPanel log={log} />}
      {tab === "raw" && <RawPanel log={log} />}
      {tab === "analytics" && (
        <AnalyticsPanel log={log} usage={usage} chips={chips} fbCount={fbCount} />
      )}
    </div>
  );
}

function RawPanel({ log }: { log: LogRow }) {
  const req = log.requests;
  const hasReq =
    !!req && typeof req === "object" && Object.keys(req as Record<string, unknown>).length > 0;
  const out = log.outputs;
  const hasOut =
    !!out && typeof out === "object" && Object.keys(out as Record<string, unknown>).length > 0;
  const reqText = hasReq ? JSON.stringify(req, null, 2) : "";
  const outText = hasOut ? JSON.stringify(out, null, 2) : "";

  async function copyText(text: string, label: string) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${label} copied`);
    } catch {
      toast.error("Copy failed");
    }
  }

  return (
    <div className="grid min-w-0 max-w-full gap-4" role="tabpanel">
      <div className="min-w-0 max-w-full">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <h3 className={sectionLabel}>1 · Sent to LLM (request)</h3>
          {hasReq && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void copyText(reqText, "Raw input")}
              aria-label="Copy raw input"
            >
              <Copy className="h-4 w-4" aria-hidden="true" />
            </Button>
          )}
        </div>
        {hasReq ? (
          <pre className={cn(codeBlock, "max-h-[60vh]")}>{reqText}</pre>
        ) : (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">
            Request payload not recorded for runs logged before this change.
          </p>
        )}
      </div>
      <div className="min-w-0 max-w-full">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <h3 className={sectionLabel}>2 · Received from LLM (response)</h3>
          {hasOut && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void copyText(outText, "Raw output")}
              aria-label="Copy raw output"
            >
              <Copy className="h-4 w-4" aria-hidden="true" />
            </Button>
          )}
        </div>
        {hasOut ? (
          <pre className={cn(codeBlock, "max-h-[60vh]")}>{outText}</pre>
        ) : (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">No response recorded.</p>
        )}
      </div>
    </div>
  );
}

function PrettyPanel({ log }: { log: LogRow }) {
  const [inputOpen, setInputOpen] = React.useState(false);
  // Denormalized snapshot details — "" on old rows degrades to "Unknown".
  const meetingDetails: [string, string][] = [
    ["Client", (log.client ?? "").trim() || "Unknown"],
    ["Meeting type", logMeetingType(log) || "Unknown"],
  ];
  return (
    <div className="grid min-w-0 max-w-full gap-4" role="tabpanel">
      <div className="min-w-0 max-w-full">
        <h3 className={sectionLabel}>Meeting</h3>
        <div className="mt-1.5 grid min-w-0 max-w-full gap-2 sm:grid-cols-2">
          {meetingDetails.map(([label, value]) => {
            const unknown = value === "Unknown";
            return (
              <div
                key={label}
                className="min-w-0 max-w-full rounded-xl border border-[#e5e7eb] p-3 dark:border-white/10"
              >
                <p className={sectionLabel}>{label}</p>
                <p
                  className={cn(
                    "mt-1 max-w-full break-words font-sans text-sm",
                    unknown
                      ? "text-[#8a8f98]"
                      : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                  )}
                  title={unknown ? undefined : value}
                >
                  {value}
                </p>
              </div>
            );
          })}
        </div>
      </div>
      <div className="min-w-0 max-w-full">
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setInputOpen((o) => !o)}
            aria-expanded={inputOpen}
            aria-label="Toggle input"
            className="flex items-center gap-1.5 rounded-lg px-1 py-0.5 text-[#4a5058] transition-colors hover:bg-[#f1f2f3] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]"
          >
            {inputOpen ? (
              <ChevronDown className="h-4 w-4 transition-transform" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-4 w-4 transition-transform" aria-hidden="true" />
            )}
            <span className={sectionLabel}>Input</span>
          </button>
        </div>
        {inputOpen && <pre className={cn(codeBlock, "max-h-40 font-sans")}>{log.input_data}</pre>}
      </div>
      <SingleModelTable log={log} />
    </div>
  );
}

function AnalyticsPanel({
  log,
  usage,
  chips,
  fbCount,
}: {
  log: LogRow;
  usage: RunUsage | null;
  chips: string[];
  fbCount: number;
}) {
  const stats: [string, string][] = [
    ["Model", usage?.model ?? log.model ?? "—"],
    ["Reasoning effort", (log.reasoning_effort ?? "").trim() || "Default"],
    ["Input tokens", fmtTokens(usage?.prompt_tokens)],
    ["Output tokens", fmtTokens(usage?.completion_tokens)],
    ["Thought tokens", fmtTokens(usage?.reasoning_tokens)],
    ["Total tokens", fmtTokens(usage?.total_tokens)],
    // Backend ships the input/output cost split (null when pricing unknown).
    ["Input cost", fmtCostBoth(usage?.input_cost_usd)],
    ["Output cost", fmtCostBoth(usage?.output_cost_usd)],
    ["Total cost", fmtCostBoth(usage?.cost_usd)],
    ["Time taken", fmtMs(usage?.duration_ms)],
    ["Agents run", String(Object.keys(log.agent_snapshot ?? {}).length)],
    ["Ratings count", String(fbCount)],
  ];
  const perAgent = usage?.per_agent ? Object.entries(usage.per_agent) : [];

  return (
    <div className="grid min-w-0 max-w-full gap-4" role="tabpanel">
      <div className="min-w-0 max-w-full">
        <h3 className={sectionLabel}>Usage stats</h3>
        <div className="mt-1.5 grid min-w-0 max-w-full grid-cols-2 sm:grid-cols-4 gap-2">
          {stats.map(([label, value]) => (
            <div
              key={label}
              className="min-w-0 max-w-full rounded-xl border border-[#e5e7eb] p-3 dark:border-white/10"
            >
              <p className={sectionLabel}>{label}</p>
              <p className="mt-1 max-w-full break-all font-mono text-sm text-[#1d1d1d] dark:text-[#F0EFEC]" title={value}>
                {value}
              </p>
            </div>
          ))}
        </div>
      </div>

      <div className="min-w-0 max-w-full">
        <h3 className={sectionLabel}>Per-agent usage</h3>
        {perAgent.length === 0 ? (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">—</p>
        ) : (
          <div className="mt-1.5 max-w-full overflow-x-hidden rounded-xl border border-[#e5e7eb] dark:border-white/10">
            <table className="w-full max-w-full font-sans text-xs">
              <thead>
                <tr className="bg-[#f1f2f3] text-left font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:bg-white/5 dark:text-[#C3C2B7]">
                  <th className="px-3 py-2">Agent</th>
                  <th className="px-3 py-2">Model</th>
                  <th className="px-3 py-2 text-right">In</th>
                  <th className="px-3 py-2 text-right">Out</th>
                  <th className="px-3 py-2 text-right">Thought</th>
                  <th className="px-3 py-2 text-right">In-cost</th>
                  <th className="px-3 py-2 text-right">Out-cost</th>
                  <th className="px-3 py-2 text-right">Cost</th>
                  <th className="px-3 py-2 text-right">Time</th>
                </tr>
              </thead>
              <tbody>
                {perAgent.map(([aid, u]) => (
                  <tr
                    key={aid}
                    className="border-t border-[#e5e7eb] text-[#1d1d1d] dark:border-white/10 dark:text-[#F0EFEC]"
                  >
                    <td className="max-w-40 break-words px-3 py-2 font-heading font-bold">
                      {log.agent_snapshot?.[aid]?.name ?? aid}
                    </td>
                    <td className="max-w-40 break-all px-3 py-2 font-mono text-[11px]">{u.model ?? "—"}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens(u.prompt_tokens)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens(u.completion_tokens)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens((u as any).reasoning_tokens)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u.input_cost_usd)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u.output_cost_usd)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u.cost_usd)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtMs(u.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div>
        <h3 className={sectionLabel}>Applied filters</h3>
        {chips.length === 0 ? (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">—</p>
        ) : (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {chips.map((c) => (
              <Badge key={c} tone="neutral">
                {c}
              </Badge>
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 className={sectionLabel}>Run info</h3>
        <pre className={codeBlock}>
          {JSON.stringify(
            { log_id: log.id, run_id: log.run_id, created_at: log.created_at },
            null,
            2,
          )}
        </pre>
      </div>
    </div>
  );
}
