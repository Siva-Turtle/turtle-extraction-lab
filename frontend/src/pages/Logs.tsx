import * as React from "react";
import { useState } from "react";
import { BarChart3, Braces, ChevronDown, ChevronLeft, ChevronRight, Copy, Sparkles } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, meetingTypeOf } from "../lib/api";
import type { LogsQueryParams } from "../lib/api";
import { fmt, fmtCostBoth, fmtMs, fmtTokens, modelLabel, serverDetail } from "../lib/format";
import type { AgentUsage, LogFilters, LogRow, RunUsage } from "../lib/logTypes";
import { buildRows, columnStats } from "../lib/compare";
import { agentsFromLog, columnsFromLogs } from "../lib/compareData";
import { groupLogs } from "../lib/logGroups";
import type { LogGroup } from "../lib/logGroups";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { MultiSelectFilter } from "../components/ui/Combobox";
import type { ModelOption } from "../components/ui/Combobox";
import { ComparisonMatrix } from "../components/compare/ComparisonMatrix";
import { SingleModelTable } from "../components/compare/SingleModelTable";
import { ReusedBadge, isFullyReused, reusedCounts } from "../components/compare/ReusedBadge";

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

/** One member row's total cost display ("—" when pricing unknown). */
function rowCostText(r: LogRow): string {
  const c = r.usage?.cost_usd;
  return typeof c === "number" && Number.isFinite(c) ? fmtCostBoth(c) : "—";
}

/** Suffix for a log row: " (reused)" when fully reused, " (k reused)" when partial, else "". */
function reusedSuffix(r: LogRow): string {
  if (isFullyReused(r)) return " (reused)";
  const { reused } = reusedCounts(r);
  if (reused > 0) return ` (${reused} reused)`;
  return "";
}

/** True when a log has any reused agents (full or partial). */
function hasAnyReuse(r: LogRow): boolean {
  return isFullyReused(r) || reusedCounts(r).reused > 0;
}

/** Sum of finite numbers, or null when none qualify. */
function sumOrNull(vals: (number | null | undefined)[]): number | null {
  let s = 0;
  let has = false;
  for (const v of vals) {
    if (typeof v === "number" && Number.isFinite(v)) {
      s += v;
      has = true;
    }
  }
  return has ? s : null;
}

/** Max of finite numbers, or null when none qualify. */
function maxOrNull(vals: (number | null | undefined)[]): number | null {
  let m: number | null = null;
  for (const v of vals) {
    if (typeof v === "number" && Number.isFinite(v)) m = m === null ? v : Math.max(m, v);
  }
  return m;
}

/** Earliest-first sort for group members (invalid timestamps sink last). */
function byCreatedAtAsc(a: LogRow, b: LogRow): number {
  const ta = new Date(a.created_at).getTime();
  const tb = new Date(b.created_at).getTime();
  if (Number.isNaN(ta) && Number.isNaN(tb)) return 0;
  if (Number.isNaN(ta)) return 1;
  if (Number.isNaN(tb)) return -1;
  return ta - tb;
}

/**
 * Labels for older rows of retried models in the Raw tab segmented control:
 * within each `model|reasoning_effort` group (created_at ascending), every
 * row but the latest is "attempt N". Single-row groups get no label.
 */
function attemptLabels(rows: LogRow[]): Map<string, string> {
  const groups = new Map<string, LogRow[]>();
  for (const r of rows) {
    const k = `${r.model ?? ""}|${r.reasoning_effort ?? ""}`;
    const list = groups.get(k);
    if (list) list.push(r);
    else groups.set(k, [r]);
  }
  const out = new Map<string, string>();
  for (const list of groups.values()) {
    if (list.length <= 1) continue;
    const sorted = [...list].sort(byCreatedAtAsc);
    sorted.forEach((r, i) => {
      if (i < sorted.length - 1) out.set(r.id, `attempt ${i + 1}`);
    });
  }
  return out;
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
  const [activeGroupKey, setActiveGroupKey] = useState<string | null>(null);
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

  const typeFiltered =
    selectedMeetingTypes.length > 0
      ? base.filter((l) => selectedMeetingTypes.includes(logMeetingType(l)))
      : base;
  const filtered =
    selectedInputTypes.length > 0
      ? typeFiltered.filter((l) => selectedInputTypes.includes(l.input_type))
      : typeFiltered;

  // One table row per run group (old rows without a group id = one-row groups).
  const groups: LogGroup[] = React.useMemo(() => groupLogs(filtered), [filtered]);

  const activeGroup = activeGroupKey
    ? (groups.find((g) => g.key === activeGroupKey) ?? null)
    : null;

  function openGroup(key: string) {
    setTab("pretty");
    setActiveGroupKey(key);
  }

  return (
    <div className="grid gap-4">
      {activeGroupKey ? (
        <LogDetail
          groupKey={activeGroupKey}
          listRowCount={activeGroup ? activeGroup.rows.length : null}
          tab={tab}
          onTab={setTab}
          onBack={() => setActiveGroupKey(null)}
        />
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
          ) : groups.length === 0 ? (
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
                    <th className="px-4 py-3">Agree</th>
                    <th className="px-4 py-3">Ratings</th>
                    <th className="px-4 py-3">Cost</th>
                    <th className="px-4 py-3">Tokens</th>
                    <th className="px-4 py-3">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => {
                    const first = g.rows[0] as LogRow;
                    const single = g.rows.length === 1;
                    const usage = single ? getUsage(first) : null;
                    const costUsd = single ? usage?.cost_usd : g.totalCostUsd;
                    const tokens = single ? usage?.total_tokens : g.totalTokens;
                    const ms = single ? usage?.duration_ms : g.maxDurationMs;
                    const hasReused = g.rows.some((r) => hasAnyReuse(r));
                    const reusedRow = g.rows.find((r) => isFullyReused(r));
                    const partialTotal = g.rows.reduce((n, r) => n + reusedCounts(r).reused, 0);
                    const singleCounts = single ? reusedCounts(first) : { reused: 0, total: 0 };
                    const singlePartial = single && !isFullyReused(first) && singleCounts.reused > 0;
                    return (
                      <tr
                        key={g.key}
                        onClick={() => openGroup(g.key)}
                        className="cursor-pointer border-b border-[#e5e7eb] last:border-0 hover:bg-[#e8fbf6]/50 dark:border-white/10 dark:hover:bg-white/5"
                      >
                        <td className="whitespace-nowrap px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {fmt(g.createdAt)}
                        </td>
                        {single ? (
                          <td className="px-4 py-3" title={first.model}>
                            <span className="inline-flex max-w-56 items-center gap-1.5">
                              <span className="max-w-48 truncate font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                                {modelLabel(first.model, first.reasoning_effort ?? "")}
                                {reusedSuffix(first)}
                              </span>
                              {isFullyReused(first) && <ReusedBadge log={first} />}
                              {singlePartial && (
                                <Badge tone="warning">{singleCounts.reused} reused</Badge>
                              )}
                            </span>
                          </td>
                        ) : (
                          <td
                            className="px-4 py-3"
                            title={g.rows.map((r) => `${modelLabel(r.model, r.reasoning_effort ?? "")}${reusedSuffix(r)} — ${rowCostText(r)}`).join("\n")}
                          >
                            <span className="inline-flex max-w-56 flex-wrap items-center gap-1">
                              {g.rows.slice(0, 3).map((r) => (
                                <span key={r.id} title={r.model}>
                                  <Badge tone="neutral">{modelLabel(r.model, r.reasoning_effort ?? "")}{reusedSuffix(r)}</Badge>
                                </span>
                              ))}
                              {g.rows.length > 3 && (
                                <span className="font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                                  +{g.rows.length - 3}
                                </span>
                              )}
                              {reusedRow && <ReusedBadge log={reusedRow} />}
                              {!reusedRow && partialTotal > 0 && (
                                <Badge tone="warning">{partialTotal} reused</Badge>
                              )}
                            </span>
                          </td>
                        )}
                        <td className="max-w-48 truncate px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]" title={g.effortLabel}>
                          {g.effortLabel}
                        </td>
                        <td className="px-4 py-3">
                          <Badge tone="brand">{first.input_type}</Badge>
                        </td>
                        <td className="px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {Object.keys(first.agent_snapshot ?? {}).length}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {g.agreePct === null ? "—" : `${Math.round(g.agreePct)}%`}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          👍{g.up} 👎{g.down}
                        </td>
                        <td
                          className="whitespace-nowrap px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]"
                          title={hasReused ? "includes reused output (not re-charged)" : undefined}
                        >
                          {fmtCostBoth(costUsd)}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {fmtTokens(tokens)}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                          {fmtMs(ms)}
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
  groupKey,
  listRowCount,
  tab,
  onTab,
  onBack,
}: {
  groupKey: string;
  /** Member rows the list group held (a filter may hide some); null when unknown. */
  listRowCount: number | null;
  tab: DrawerTab;
  onTab: (t: DrawerTab) => void;
  onBack: () => void;
}) {
  // Full group fetch (no list filters): matches run_group_id OR id.
  const groupQuery = useQuery({
    queryKey: ["log-group", groupKey],
    queryFn: async () =>
      (
        await api.get("/logs", {
          params: { run_group_ids: groupKey },
          paramsSerializer: { indexes: null },
        })
      ).data as LogRow[],
    staleTime: 30 * 1000,
  });

  const rows = React.useMemo(
    () => [...(groupQuery.data ?? [])].sort(byCreatedAtAsc),
    [groupQuery.data],
  );
  const loading = groupQuery.isLoading || (groupQuery.isFetching && rows.length === 0);

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

      {loading ? (
        <Card>
          <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
        </Card>
      ) : groupQuery.isError ? (
        <Card>
          <p role="alert" className="font-heading text-sm text-[#b91c1c] dark:text-[#f87171]">
            {serverDetail(groupQuery.error)}
          </p>
          <div className="mt-3">
            <Button
              variant="secondary"
              size="sm"
              loading={groupQuery.isFetching}
              onClick={() => void groupQuery.refetch()}
            >
              Retry
            </Button>
          </div>
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            No logs found for this run.
          </p>
        </Card>
      ) : (
        <>
          {tab === "pretty" && <PrettyPanel rows={rows} listRowCount={listRowCount} />}
          {tab === "raw" && <RawPanel rows={rows} />}
          {tab === "analytics" && <AnalyticsPanel rows={rows} groupKey={groupKey} />}
        </>
      )}
    </div>
  );
}

function RawSingle({ log }: { log: LogRow }) {
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

function RawPanel({ rows }: { rows: LogRow[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Every row stays listed, including older attempts of retried models; the
  // matrix (columnsFromLogs) shows only the latest per model+effort.
  const attempts = React.useMemo(() => attemptLabels(rows), [rows]);
  if (rows.length <= 1) return <RawSingle log={rows[0] as LogRow} />;
  const selected = rows.find((r) => r.id === selectedId) ?? (rows[0] as LogRow);
  return (
    <div className="grid min-w-0 max-w-full gap-4" role="tabpanel">
      <div
        role="tablist"
        aria-label="Model"
        className="flex flex-wrap items-center gap-1 rounded-full border border-[#e5e7eb] bg-[#f1f2f3] p-1 dark:border-white/10 dark:bg-white/5"
      >
        {rows.map((r) => {
          const isSel = r.id === selected.id;
          const attempt = attempts.get(r.id);
          const full = isFullyReused(r);
          const { reused: partialCount } = reusedCounts(r);
          const partial = !full && partialCount > 0;
          const label = `${modelLabel(r.model, r.reasoning_effort ?? "")}${reusedSuffix(r)}`;
          return (
            <button
              key={r.id}
              role="tab"
              aria-selected={isSel}
              title={attempt ? `${label} · ${attempt} (${r.created_at})` : full ? `${label} (${r.model})` : r.model}
              onClick={() => setSelectedId(r.id)}
              className={cn(
                "flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-4 py-2 font-heading text-xs font-bold transition-colors",
                isSel
                  ? "bg-white text-[#1d1d1d] shadow-[0_1px_4px_rgba(29,29,29,0.12)] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
                  : "text-[#4a5058] hover:text-[#1d1d1d] dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]",
              )}
            >
              <span className="max-w-40 truncate font-mono">{label}</span>
              {full && (
                <span className="inline-flex">
                  <ReusedBadge log={r} />
                </span>
              )}
              {partial && (
                <span className="inline-flex">
                  <Badge tone="warning">{partialCount} reused</Badge>
                </span>
              )}
              {attempt && (
                <span className="rounded-full border border-[#e5e7eb] px-1.5 py-0.5 font-sans text-[10px] font-normal dark:border-white/10">
                  {attempt}
                </span>
              )}
            </button>
          );
        })}
      </div>
      <RawSingle key={selected.id} log={selected} />
    </div>
  );
}

function PrettyPanel({
  rows,
  listRowCount,
}: {
  rows: LogRow[];
  listRowCount: number | null;
}) {
  const log = rows[0] as LogRow;
  const [inputOpen, setInputOpen] = useState(false);
  const columns = React.useMemo(() => columnsFromLogs(rows), [rows]);
  const agents = React.useMemo(() => agentsFromLog(log), [log, rows]);
  const multi = rows.length > 1;
  const showAllNote = listRowCount !== null && rows.length > listRowCount;
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
      {multi && showAllNote && (
        <p className="font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
          Showing all {rows.length} models of this run
        </p>
      )}
      {multi ? (
        <ComparisonMatrix columns={columns} agents={agents} editable showDoneBadge={false} />
      ) : (
        <SingleModelTable log={log} />
      )}
    </div>
  );
}

function PerAgentTable({
  entries,
}: {
  entries: { key: string; agentName: string; model: string; u: AgentUsage }[];
}) {
  return (
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
          {entries.map(({ key, agentName, model, u }) => (
            <tr
              key={key}
              className="border-t border-[#e5e7eb] text-[#1d1d1d] dark:border-white/10 dark:text-[#F0EFEC]"
            >
              <td className="max-w-40 break-words px-3 py-2 font-heading font-bold">
                {agentName}
              </td>
              <td className="max-w-40 break-all px-3 py-2 font-mono text-[11px]">{model}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens(u.prompt_tokens)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens(u.completion_tokens)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens((u as unknown as { reasoning_tokens?: unknown }).reasoning_tokens)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u.input_cost_usd)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u.output_cost_usd)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u.cost_usd)}</td>
              <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtMs(u.duration_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AnalyticsSingle({
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
  const perAgent = usage?.per_agent
    ? Object.entries(usage.per_agent).map(([aid, u]) => ({
        key: aid,
        agentName: log.agent_snapshot?.[aid]?.name ?? aid,
        model: u.model ?? "—",
        u,
      }))
    : [];

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
          <PerAgentTable entries={perAgent} />
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

function AnalyticsPanel({ rows, groupKey }: { rows: LogRow[]; groupKey: string }) {
  const log = rows[0] as LogRow;
  const columns = React.useMemo(() => columnsFromLogs(rows), [rows]);
  const agents = React.useMemo(() => agentsFromLog(log), [log, rows]);
  const built = React.useMemo(() => buildRows(agents, columns), [agents, columns]);

  if (rows.length === 1) {
    const usage = getUsage(log);
    const chips = filterChips(log.filters);
    const fbCount = ratingsCount(log);
    return <AnalyticsSingle log={log} usage={usage} chips={chips} fbCount={fbCount} />;
  }

  const usages = rows.map((r) => getUsage(r));
  const stats: [string, string][] = [
    ["Models", `${rows.length} models`],
    [
      "Reasoning effort",
      (() => {
        const efforts = new Set(rows.map((r) => (r.reasoning_effort ?? "").trim()));
        if (efforts.size !== 1) return "mixed";
        return [...efforts][0] || "Default";
      })(),
    ],
    ["Input tokens", fmtTokens(sumOrNull(usages.map((u) => u?.prompt_tokens)))],
    ["Output tokens", fmtTokens(sumOrNull(usages.map((u) => u?.completion_tokens)))],
    ["Thought tokens", fmtTokens(sumOrNull(usages.map((u) => u?.reasoning_tokens)))],
    ["Total tokens", fmtTokens(sumOrNull(usages.map((u) => u?.total_tokens)))],
    ["Input cost", fmtCostBoth(sumOrNull(usages.map((u) => u?.input_cost_usd)))],
    ["Output cost", fmtCostBoth(sumOrNull(usages.map((u) => u?.output_cost_usd)))],
    ["Total cost", fmtCostBoth(sumOrNull(usages.map((u) => u?.cost_usd)))],
    ["Time taken", fmtMs(maxOrNull(usages.map((u) => u?.duration_ms)))],
    ["Agents run", String(Object.keys(log.agent_snapshot ?? {}).length)],
    ["Ratings count", String(rows.reduce((n, r) => n + ratingsCount(r), 0))],
  ];
  const perAgent: { key: string; agentName: string; model: string; u: AgentUsage }[] = [];
  for (const r of rows) {
    const pa = r.usage?.per_agent;
    if (!pa || typeof pa !== "object") continue;
    for (const [aid, u] of Object.entries(pa)) {
      perAgent.push({
        key: `${r.id}|${aid}`,
        agentName: r.agent_snapshot?.[aid]?.name ?? aid,
        model: u.model ?? "—",
        u,
      });
    }
  }
  const chips = filterChips(log.filters);

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
        <h3 className={sectionLabel}>Model comparison</h3>
        <div className="mt-1.5 max-w-full overflow-x-auto rounded-xl border border-[#e5e7eb] dark:border-white/10">
          <table className="w-full min-w-[1000px] font-sans text-xs">
            <thead>
              <tr className="bg-[#f1f2f3] text-left font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:bg-white/5 dark:text-[#C3C2B7]">
                <th className="px-3 py-2">Model</th>
                <th className="px-3 py-2 text-right">In</th>
                <th className="px-3 py-2 text-right">Out</th>
                <th className="px-3 py-2 text-right">Thought</th>
                <th className="px-3 py-2 text-right">In-cost</th>
                <th className="px-3 py-2 text-right">Out-cost</th>
                <th className="px-3 py-2 text-right">Cost</th>
                <th className="px-3 py-2 text-right">Time</th>
                <th className="px-3 py-2 text-right">Agree %</th>
                <th className="px-3 py-2 text-right">👍</th>
                <th className="px-3 py-2 text-right">👎</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((col) => {
                const l = col.log as LogRow;
                const u = getUsage(l);
                const s = columnStats(built, col.key);
                const full = isFullyReused(l);
                const { reused: partialCount } = reusedCounts(l);
                const partial = !full && partialCount > 0;
                return (
                  <tr
                    key={col.key}
                    className="border-t border-[#e5e7eb] text-[#1d1d1d] dark:border-white/10 dark:text-[#F0EFEC]"
                  >
                    <td className="max-w-40 break-all px-3 py-2 font-mono text-[11px]" title={l.model}>
                      <span className="inline-flex flex-wrap items-center gap-1.5">
                        <span>
                          {modelLabel(l.model, l.reasoning_effort ?? "")}
                          {reusedSuffix(l)}
                        </span>
                        {full && <ReusedBadge log={l} />}
                        {partial && <Badge tone="warning">{partialCount} reused</Badge>}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens(u?.prompt_tokens)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens(u?.completion_tokens)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtTokens(u?.reasoning_tokens)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u?.input_cost_usd)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u?.output_cost_usd)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtCostBoth(u?.cost_usd)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{fmtMs(u?.duration_ms)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{`${s.agreePct.toFixed(0)}%`}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{s.up}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-mono">{s.down}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="min-w-0 max-w-full">
        <h3 className={sectionLabel}>Per-agent usage</h3>
        {perAgent.length === 0 ? (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">—</p>
        ) : (
          <PerAgentTable entries={perAgent} />
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
            {
              run_group_id: groupKey,
              log_ids: rows.map((r) => r.id),
              run_ids: rows.map((r) => r.run_id),
              created_at: log.created_at,
            },
            null,
            2,
          )}
        </pre>
      </div>
    </div>
  );
}
