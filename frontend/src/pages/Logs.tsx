import * as React from "react";
import { useState } from "react";
import { BarChart3, Braces, Check, ChevronDown, Sparkles } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Card, CardTitle } from "../components/ui/Card";
import { Drawer } from "../components/ui/Drawer";
import { fieldInput, fieldLabel } from "../components/ui/Modal";
import { PageHeader } from "../components/ui/PageHeader";

type AgentUsage = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number | null;
  input_cost_usd?: number | null;
  output_cost_usd?: number | null;
  duration_ms: number;
  model: string;
};

type RunUsage = AgentUsage & {
  per_agent: Record<string, AgentUsage>;
};

type LogFilters = {
  client_id?: string;
  client_name?: string;
  meeting_title?: string;
  meeting_date?: string;
  meeting_id?: string;
};

type LogRow = {
  id: string;
  run_id: string;
  input_type: string;
  input_data: string;
  model: string;
  agent_snapshot: Record<string, { name: string; attributes: SnapshotAttr[] }>;
  attribute_snapshot?: Record<string, unknown>;
  outputs: Record<string, unknown>;
  requests?: Record<string, unknown>;
  feedback: Record<string, Record<string, { rating: string; remarks: string }>>;
  usage?: RunUsage;
  filters?: LogFilters;
  created_at: string;
};

type PrettyAttr = {
  value?: unknown;
  confidence?: unknown;
  confidence_type?: unknown;
  evidence?: unknown;
};

/**
 * Frozen attribute definitions ship in two shapes: old
 * {name,type,description,json_schema} and new {name,type,description,enum_values}.
 * Everything but name is optional so neither shape can crash the renderer.
 */
type SnapshotAttr = {
  name: string;
  type?: unknown;
  description?: unknown;
  json_schema?: unknown;
  enum_values?: unknown;
};

/** Enum chips whenever enum_values is present (new shape); [] otherwise. Never throws. */
function enumChipsOf(a: unknown): string[] {
  if (!a || typeof a !== "object") return [];
  const v = (a as { enum_values?: unknown }).enum_values;
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === "string" && x.trim() !== "");
}

/** Snapshot attribute definitions for one agent; [] when missing or malformed. Never throws. */
function snapshotAttrs(log: LogRow, agentId: string): SnapshotAttr[] {
  const attrs = (log.agent_snapshot?.[agentId] as { attributes?: unknown } | undefined)
    ?.attributes;
  if (!Array.isArray(attrs)) return [];
  return attrs.filter(
    (a): a is SnapshotAttr =>
      !!a && typeof a === "object" && typeof (a as { name?: unknown }).name === "string",
  );
}

type DrawerTab = "pretty" | "raw" | "analytics";

function fmt(ts: string) {
  try {
    return new Date(ts).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
  } catch {
    return ts;
  }
}

function fmtTokens(n: unknown): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-IN") : "—";
}

function fmtMs(ms: unknown): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`;
}

function fmtCost(cost: unknown): string {
  if (typeof cost !== "number" || !Number.isFinite(cost)) return "—";
  return `$${cost.toFixed(6)}`;
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

function prettyEntries(out: unknown): [string, PrettyAttr][] {
  if (!out || typeof out !== "object") return [];
  return (Object.entries(out as Record<string, unknown>) as [string, unknown][])
    .filter(([k]) => k !== "_error")
    .map(([k, v]) => [k, (v && typeof v === "object" ? v : {}) as PrettyAttr]);
}

const sectionLabel = "font-heading text-xs font-bold uppercase tracking-wide text-[#4a5058] dark:text-[#C3C2B7]";
const codeBlock =
  "mt-1.5 max-h-60 overflow-auto rounded-xl bg-[#f1f2f3] p-3 font-mono text-xs text-[#1d1d1d] dark:bg-white/5 dark:text-[#F0EFEC]";

const TABS: { id: DrawerTab; label: string; icon: typeof Sparkles }[] = [
  { id: "pretty", label: "Pretty", icon: Sparkles },
  { id: "raw", label: "Raw", icon: Braces },
  { id: "analytics", label: "Analytics", icon: BarChart3 },
];

const INPUT_FILTERS = ["transcription", "messages", "mail"];

function InputTypeFilter({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}): React.JSX.Element {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const options = ["", ...INPUT_FILTERS];

  return (
    <div ref={rootRef} className="relative mt-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        role="combobox"
        aria-expanded={open}
        aria-label="Filter by input type"
        className={cn(
          "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-2 font-sans text-sm",
          "hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1",
          "dark:border-white/10 dark:bg-[#2e2e2e] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]",
        )}
      >
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-left",
            value ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98] dark:text-[#898781]",
          )}
        >
          {value || "All input types"}
        </span>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10">
          <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
        </span>
      </button>
      {open && (
        <ul
          role="listbox"
          aria-label="Filter by input type"
          className="absolute inset-x-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-2xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] animate-[turtle-fade-in_120ms_ease-out] dark:border-white/10 dark:bg-[#1a1a1a]"
        >
          {options.map((o) => (
            <li key={o || "all"} role="option" aria-selected={value === o}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(o);
                  setOpen(false);
                }}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]"
              >
                <span className="min-w-0 flex-1 truncate">{o || "All input types"}</span>
                {value === o && (
                  <Check className="h-4 w-4 shrink-0 text-[#0d5c4a] dark:text-[#2fdebf]" aria-hidden="true" />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Logs() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["logs"],
    queryFn: async () => (await api.get("/logs")).data as LogRow[],
  });
  const [activeId, setActiveId] = useState<string | null>(null);
  const [tab, setTab] = useState<DrawerTab>("pretty");
  const [search, setSearch] = useState("");
  const [inputFilter, setInputFilter] = useState("");

  const active = activeId ? (data.find((l) => l.id === activeId) ?? null) : null;

  function openLog(id: string) {
    setTab("pretty");
    setActiveId(id);
  }

  const q = search.trim().toLowerCase();
  const filtered = data.filter((l) => {
    if (inputFilter && l.input_type !== inputFilter) return false;
    if (!q) return true;
    const hay = [
      l.model ?? "",
      l.run_id ?? "",
      l.input_type ?? "",
      l.filters?.client_name ?? "",
      l.filters?.client_id ?? "",
      l.filters?.meeting_title ?? "",
      l.filters?.meeting_date ?? "",
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });

  return (
    <div className="grid gap-4">
      <PageHeader title="Run logs" />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className={fieldLabel}>
            Search
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Model, client, meeting…"
              aria-label="Search logs"
              className={fieldInput}
            />
          </label>
          <div className={fieldLabel}>
            Input type
            <InputTypeFilter value={inputFilter} onChange={setInputFilter} />
          </div>
        </div>
      </Card>

      {isLoading ? (
        <Card>
          <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
        </Card>
      ) : data.length === 0 ? (
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
          <table className="w-full min-w-[840px] text-left text-sm">
            <thead>
              <tr className="border-b border-[#e5e7eb] font-heading text-xs font-bold uppercase tracking-wide text-[#8a8f98] dark:border-white/10">
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3">Model</th>
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
                    <td className="px-4 py-3">
                      <Badge tone="brand">{l.input_type}</Badge>
                    </td>
                    <td className="px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                      {Object.keys(l.agent_snapshot ?? {}).length}
                    </td>
                    <td className="px-4 py-3 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                      {fbCount}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                      {usage ? fmtCost(usage.cost_usd) : "—"}
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

      {active && (
        <LogDrawer log={active} tab={tab} onTab={setTab} onClose={() => setActiveId(null)} />
      )}
    </div>
  );
}

function LogDrawer({
  log,
  tab,
  onTab,
  onClose,
}: {
  log: LogRow;
  tab: DrawerTab;
  onTab: (t: DrawerTab) => void;
  onClose: () => void;
}) {
  const fbCount = ratingsCount(log);
  const usage = getUsage(log);
  const chips = filterChips(log.filters);

  return (
    <Drawer
      onClose={onClose}
      title={
        <div className="grid gap-1">
          <p className="font-heading text-base font-bold text-[#1d1d1d] dark:text-[#F0EFEC]">
            Run {log.run_id.slice(0, 8)}
          </p>
          <p className="truncate font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">{log.model}</p>
          <p className="font-heading text-xs text-[#8a8f98]">{fmt(log.created_at)}</p>
        </div>
      }
    >
      <div className="grid gap-4 animate-[turtle-fade-in_150ms_ease-out]">
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

        {tab === "pretty" && <PrettyPanel log={log} fbCount={fbCount} />}
        {tab === "raw" && <RawPanel log={log} />}
        {tab === "analytics" && (
          <AnalyticsPanel log={log} usage={usage} chips={chips} fbCount={fbCount} />
        )}
      </div>
    </Drawer>
  );
}

function RawPanel({ log }: { log: LogRow }) {
  const req = log.requests;
  const hasReq =
    !!req && typeof req === "object" && Object.keys(req as Record<string, unknown>).length > 0;
  const out = log.outputs;
  const hasOut =
    !!out && typeof out === "object" && Object.keys(out as Record<string, unknown>).length > 0;
  return (
    <div className="grid gap-4" role="tabpanel">
      <div>
        <h3 className={sectionLabel}>1 · Sent to LLM (request)</h3>
        {hasReq ? (
          <pre className={cn(codeBlock, "max-h-[60vh]")}>{JSON.stringify(req, null, 2)}</pre>
        ) : (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">
            Request payload not recorded for runs logged before this change.
          </p>
        )}
      </div>
      <div>
        <h3 className={sectionLabel}>2 · Received from LLM (response)</h3>
        {hasOut ? (
          <pre className={cn(codeBlock, "max-h-[60vh]")}>{JSON.stringify(out, null, 2)}</pre>
        ) : (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">No response recorded.</p>
        )}
      </div>
    </div>
  );
}

function PrettyPanel({ log, fbCount }: { log: LogRow; fbCount: number }) {
  return (
    <div className="grid gap-4" role="tabpanel">
      <div>
        <h3 className={sectionLabel}>Input</h3>
        <pre className={cn(codeBlock, "max-h-40 whitespace-pre-wrap font-sans")}>{log.input_data}</pre>
      </div>
      <div className="grid gap-3">
        {Object.entries(log.outputs ?? {}).map(([agentId, out]) => {
          const agentName = log.agent_snapshot?.[agentId]?.name ?? agentId;
          if (out && typeof out === "object" && "_error" in (out as Record<string, unknown>)) {
            const err = (out as Record<string, unknown>)._error;
            return (
              <div
                key={agentId}
                className="rounded-2xl border border-[#ef4444]/40 bg-[#fdecec] p-4 dark:bg-[#ef4444]/10"
              >
                <CardTitle>{agentName}</CardTitle>
                <p className="mt-1 font-sans text-sm text-[#b91c1c] dark:text-[#f87171]">
                  Agent failed: {String(err)}
                </p>
              </div>
            );
          }
          const entries = prettyEntries(out);
          const snapAttrs = snapshotAttrs(log, agentId);
          return (
            <div key={agentId} className="rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
              <CardTitle>{agentName}</CardTitle>
              {snapAttrs.length > 0 && (
                <div className="mt-2">
                  <p className={sectionLabel}>Attributes (snapshot)</p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {snapAttrs.map((sa, i) => (
                      <span
                        key={`${sa.name}-${i}`}
                        title={typeof sa.description === "string" ? sa.description : undefined}
                        className="inline-flex items-center gap-1.5 rounded-full bg-[#f1f2f3] px-2.5 py-1 font-heading text-[11px] font-bold text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                      >
                        {sa.name} · {String(sa.type ?? "?")}
                        {enumChipsOf(sa).map((c) => (
                          <Badge key={c} tone="neutral">{c}</Badge>
                        ))}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div className="mt-2 grid gap-2">
                {entries.map(([attr, r]) => (
                  <div key={attr} className="rounded-xl bg-[#f1f2f3] p-3 dark:bg-white/5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-heading text-sm font-bold text-[#1d1d1d] dark:text-[#F0EFEC]">
                        {attr}
                      </span>
                      <Badge tone="brand">{String(r.confidence_type ?? "?")}</Badge>
                      <span className="font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                        {typeof r.confidence === "number" ? r.confidence.toFixed(2) : "?"}
                      </span>
                    </div>
                    <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[#e5e7eb] dark:bg-white/10">
                      <div
                        className="h-1.5 rounded-full bg-[#2fdebf]"
                        style={{
                          width: `${typeof r.confidence === "number" ? Math.round(r.confidence * 100) : 0}%`,
                        }}
                      />
                    </div>
                    <p className="mt-2 font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]">
                      <span
                        className={cn(sectionLabel, "inline")}
                        style={{ fontSize: "inherit" }}
                      >
                        Value ·{" "}
                      </span>
                      {String(r.value ?? "—")}
                    </p>
                    {typeof r.evidence === "string" && r.evidence !== "" && (
                      <p className="mt-1 border-l-2 border-[#2fdebf] pl-2 font-sans text-sm italic text-[#4a5058] dark:text-[#C3C2B7]">
                        “{r.evidence}”
                      </p>
                    )}
                  </div>
                ))}
                {entries.length === 0 && (
                  <p className="font-heading text-xs text-[#8a8f98]">Agent returned no attributes.</p>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div>
        <h3 className={sectionLabel}>Feedback</h3>
        {fbCount === 0 ? (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">No ratings yet.</p>
        ) : (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {Object.entries(log.feedback).map(([agent, attrs]) =>
              Object.entries(attrs).map(([attr, f]) => (
                <span
                  key={`${agent}-${attr}`}
                  title={f.remarks || undefined}
                  className="inline-flex items-center gap-1.5 rounded-full bg-[#f1f2f3] px-2.5 py-1 font-heading text-[11px] font-bold text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                >
                  {agent} / {attr} {f.rating === "up" ? "👍" : "👎"}
                </span>
              )),
            )}
          </div>
        )}
      </div>
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
    ["Input tokens", fmtTokens(usage?.prompt_tokens)],
    ["Output tokens", fmtTokens(usage?.completion_tokens)],
    ["Total tokens", fmtTokens(usage?.total_tokens)],
    // Backend ships the input/output cost split (null when pricing unknown).
    ["Input cost", fmtCost(usage?.input_cost_usd)],
    ["Output cost", fmtCost(usage?.output_cost_usd)],
    ["Total cost", fmtCost(usage?.cost_usd)],
    ["Time taken", fmtMs(usage?.duration_ms)],
    ["Agents run", String(Object.keys(log.agent_snapshot ?? {}).length)],
    ["Ratings count", String(fbCount)],
  ];
  const perAgent = usage?.per_agent ? Object.entries(usage.per_agent) : [];

  return (
    <div className="grid gap-4" role="tabpanel">
      <div>
        <h3 className={sectionLabel}>Usage stats</h3>
        <div className="mt-1.5 grid grid-cols-2 gap-2">
          {stats.map(([label, value]) => (
            <div
              key={label}
              className="rounded-xl border border-[#e5e7eb] p-3 dark:border-white/10"
            >
              <p className={sectionLabel}>{label}</p>
              <p className="mt-1 truncate font-mono text-sm text-[#1d1d1d] dark:text-[#F0EFEC]" title={value}>
                {value}
              </p>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className={sectionLabel}>Per-agent usage</h3>
        {perAgent.length === 0 ? (
          <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">—</p>
        ) : (
          <div className="mt-1.5 overflow-x-auto rounded-xl border border-[#e5e7eb] dark:border-white/10">
            <table className="w-full min-w-[720px] font-sans text-xs">
              <thead>
                <tr className="bg-[#f1f2f3] text-left font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:bg-white/5 dark:text-[#C3C2B7]">
                  <th className="px-3 py-2">Agent</th>
                  <th className="px-3 py-2">Model</th>
                  <th className="px-3 py-2 text-right">In</th>
                  <th className="px-3 py-2 text-right">Out</th>
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
                    <td className="px-3 py-2 font-heading font-bold">
                      {log.agent_snapshot?.[aid]?.name ?? aid}
                    </td>
                    <td className="truncate px-3 py-2 font-mono text-[11px]">{u.model ?? "—"}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtTokens(u.prompt_tokens)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtTokens(u.completion_tokens)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtCost(u.input_cost_usd)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtCost(u.output_cost_usd)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtCost(u.cost_usd)}</td>
                    <td className="px-3 py-2 text-right font-mono">{fmtMs(u.duration_ms)}</td>
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
