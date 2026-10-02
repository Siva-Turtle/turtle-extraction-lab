import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Eye, Play, ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";
import { api, meetingTypeOf, REASONING_EFFORTS } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardTitle } from "../components/ui/Card";
import { ModelCombobox, MultiSelectFilter, SingleSelectFilter } from "../components/ui/Combobox";
import { Modal, fieldInput, fieldLabel } from "../components/ui/Modal";
import { PageHeader } from "../components/ui/PageHeader";
import type { Agent } from "./Agents";

type AttrResult = {
  value?: string | number | boolean | null;
  confidence?: number;
  confidence_type?: string;
  evidence?: string;
};

type RunOutputs = Record<string, Record<string, AttrResult> & { _error?: string }>;

/** Identifier-kind output shape: {selected_agents: string[]}. Null when not identifier. */
function selectedAgentsOf(out: unknown): string[] | null {
  if (!out || typeof out !== "object" || Array.isArray(out)) return null;
  const v = (out as Record<string, unknown>).selected_agents;
  if (!Array.isArray(v)) return null;
  return v
    .filter((x): x is string => typeof x === "string")
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

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

function serverDetail(e: unknown): string {
  if (typeof e === "object" && e !== null && "response" in e) {
    const resp = (e as { response?: { data?: { detail?: unknown } } }).response;
    if (resp && typeof resp.data === "object" && resp.data !== null && "detail" in resp.data) {
      const d = (resp.data as { detail?: unknown }).detail;
      if (typeof d === "string" && d.trim() !== "") return d;
    }
  }
  if (e instanceof Error && e.message) return e.message;
  return "Request failed";
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
}: {
  agents: Agent[];
  selected: string[];
  onChange: (ids: string[]) => void;
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
    <div>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="Agents to run"
          className={cn(
            "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-2 font-sans text-sm",
            "hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1",
            "dark:border-white/10 dark:bg-[#2e2e2e] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]",
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

function RatingBox({ runId, agentName, attrName }: { runId: string; agentName: string; attrName: string }) {
  const qc = useQueryClient();
  const [rating, setRating] = React.useState<"up" | "down" | null>(null);
  const [remarks, setRemarks] = React.useState("");
  const [done, setDone] = React.useState(false);

  const save = useMutation({
    mutationFn: async () =>
      (await api.post(`/runs/${runId}/feedback`, {
        agent_name: agentName,
        attribute_name: attrName,
        rating,
        remarks,
      })).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["logs"] });
      setDone(true);
      toast.success("Feedback saved to the run log");
    },
    onError: () => toast.error("Could not save feedback"),
  });

  if (done) {
    return (
      <p className="mt-3">
        <Badge tone={rating === "up" ? "success" : "danger"}>rated {rating === "up" ? "👍" : "👎"} · saved</Badge>
      </p>
    );
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[#e5e7eb] pt-3 dark:border-white/10">
      <button
        onClick={() => setRating("up")}
        title="Thumbs up"
        aria-pressed={rating === "up"}
        className={cn(
          "flex h-9 w-9 items-center justify-center rounded-full border transition-colors",
          rating === "up"
            ? "border-[#22c55e] bg-[#e9f9ef] text-[#15803d]"
            : "border-[#e5e7eb] text-[#4a5058] hover:border-[#22c55e] dark:border-white/10 dark:text-[#C3C2B7]",
        )}
      >
        <ThumbsUp className="h-4 w-4" aria-hidden="true" />
      </button>
      <button
        onClick={() => setRating("down")}
        title="Thumbs down"
        aria-pressed={rating === "down"}
        className={cn(
          "flex h-9 w-9 items-center justify-center rounded-full border transition-colors",
          rating === "down"
            ? "border-[#ef4444] bg-[#fdecec] text-[#b91c1c]"
            : "border-[#e5e7eb] text-[#4a5058] hover:border-[#ef4444] dark:border-white/10 dark:text-[#C3C2B7]",
        )}
      >
        <ThumbsDown className="h-4 w-4" aria-hidden="true" />
      </button>
      <input
        value={remarks}
        onChange={(e) => setRemarks(e.target.value)}
        placeholder="Remarks for this attribute…"
        aria-label="Remarks"
        className="h-9 min-w-40 flex-1 rounded-full border border-[#e5e7eb] bg-white px-3 font-sans text-sm text-[#1d1d1d] placeholder:text-[#8a8f98] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC]"
      />
      <Button variant="secondary" size="sm" loading={save.isPending} onClick={() => (rating ? save.mutate() : toast.error("Pick 👍 or 👎 first"))}>
        Save rating
      </Button>
    </div>
  );
}

export default function TestLab() {
  const [model, setModel] = React.useState("");
  const [reasoningEffort, setReasoningEffort] = React.useState("");
  const [selected, setSelected] = React.useState<string[]>([]);
  const [runId, setRunId] = React.useState<string | null>(null);
  const [outputs, setOutputs] = React.useState<RunOutputs | null>(null);
  const [clientId, setClientId] = React.useState("");
  const [selectedMeetingTypes, setSelectedMeetingTypes] = React.useState<string[]>([]);
  const [meetingDate, setMeetingDate] = React.useState("");
  const [meetingId, setMeetingId] = React.useState("");
  const [previewOpen, setPreviewOpen] = React.useState(false);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as Agent[],
  });
  const enabledAgents = agents.filter((a) => a.is_enabled !== false);

  React.useEffect(() => {
    setSelected((s) => s.filter((id) => agents.some((a) => a.id === id && a.is_enabled !== false)));
  }, [agents]);

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

  const run = useMutation({
    mutationFn: async () => {
      // Denormalized meeting snapshot for the run log (plain strings only):
      // client name from the picker, meeting type derived from the picked
      // instance's full title (falls back to the type filter), meeting title
      // always the raw full instance title (unchanged backend contract).
      const clientName =
        (clientsQuery.data ?? []).find((c) => c.id === clientId)?.name ?? "";
      const picked = (meetingsQuery.data?.meetings ?? []).find((m) => m.id === meetingId);
      const instanceTitle = (picked?.title ?? transcriptQuery.data?.title ?? "").trim();
      return (await api.post("/runs", {
        input_type: "transcription",
        meeting_id: meetingId,
        agent_ids: selected,
        model,
        reasoning_effort: reasoningEffort,
        client: clientName,
        meeting_type: meetingTypeOf(instanceTitle) || selectedMeetingTypes[0] || "",
        meeting_title: instanceTitle,
      })).data as { id: string; outputs: RunOutputs };
    },
    onSuccess: (res) => {
      setRunId(res.id);
      setOutputs(res.outputs);
      toast.success("Run complete — rate each attribute below");
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Run failed"),
  });

  function canRun() {
    if (!meetingId) return "Select a meeting first";
    if (transcriptQuery.isFetching || transcriptQuery.isLoading) return "Transcript still loading…";
    if (!transcriptQuery.data?.transcription?.trim()) return "Transcript still loading…";
    if (!model.trim()) return "Enter an OpenRouter model id";
    if (selected.length === 0) return "Select at least one agent";
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

  return (
    <div className="grid gap-4">
      <PageHeader title="Test Lab" />

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
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className={fieldLabel}>OpenRouter model</span>
                <KeyStatusBadge />
              </div>
              <ModelCombobox value={model} onChange={setModel} />
            </div>
            <div>
              <span className={fieldLabel}>Reasoning effort</span>
              <SingleSelectFilter
                value={reasoningEffort}
                onChange={setReasoningEffort}
                options={REASONING_EFFORTS.map((v) => ({
                  value: v,
                  label: v,
                }))}
                placeholder="Default (no effort)"
                ariaLabel="Reasoning effort"
                filterPlaceholder="Search efforts…"
                emptyText="No matches."
              />
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <span className={fieldLabel}>Client</span>
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
              <span className={fieldLabel}>Meeting type</span>
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
            <label className={fieldLabel}>
              Meeting start date
              <input
                type="date"
                value={meetingDate}
                onChange={(e) => handleDateChange(e.target.value)}
                disabled={dateDisabled}
                aria-label="Meeting start date"
                className={cn(fieldInput, "h-11 disabled:cursor-not-allowed disabled:opacity-50")}
              />
            </label>
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
            <span className={fieldLabel}>Meeting</span>
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
            Agents to run
            <div className="mt-1.5">
              <AgentsMultiSelect agents={enabledAgents} selected={selected} onChange={setSelected} />
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
            <Button
              loading={run.isPending}
              onClick={() => {
                const why = canRun();
                if (why) toast.error(why);
                else run.mutate();
              }}
            >
              <Play className="h-4 w-4" aria-hidden="true" /> Run
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

      {outputs && runId && (
        <Card>
          <div className="mb-3 flex items-center gap-2">
            <CardTitle>Results</CardTitle>
            <Badge tone="neutral">run {runId.slice(0, 8)}</Badge>
          </div>
          <div className="grid gap-3">
            {Object.entries(outputs).map(([agentId, out]) => {
              const agent = agents.find((a) => a.id === agentId);
              if (out._error) {
                return (
                  <div key={agentId} className="rounded-2xl border border-[#ef4444]/40 bg-[#fdecec] p-4 dark:bg-[#ef4444]/10">
                    <CardTitle>{agent?.name ?? agentId}</CardTitle>
                    <p className="mt-1 font-sans text-sm text-[#b91c1c] dark:text-[#f87171]">Agent failed: {out._error}</p>
                  </div>
                );
              }
              const entries = (Object.entries(out) as [string, AttrResult][]).filter(([k]) => k !== "_error");
              const selected = selectedAgentsOf(out);
              if (selected !== null) {
                return (
                  <div key={agentId} className="rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
                    <CardTitle>{agent?.name ?? agentId}</CardTitle>
                    <div className="mt-2 grid gap-2">
                      {selected.length === 0 ? (
                        <p className="font-heading text-xs text-[#8a8f98]">No agents selected.</p>
                      ) : (
                        <div className="flex flex-wrap gap-1.5">
                          {selected.map((name) => (
                            <Badge key={name} tone="brand">
                              {name}
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              }
              return (
                <div key={agentId} className="rounded-2xl border border-[#e5e7eb] p-4 dark:border-white/10">
                  <CardTitle>{agent?.name ?? agentId}</CardTitle>
                  <div className="mt-2 grid gap-2">
                    {entries.map(([attr, r]) => (
                      <div key={attr} className="rounded-xl bg-[#f1f2f3] p-3 dark:bg-white/5">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-heading text-sm font-bold text-[#1d1d1d] dark:text-[#F0EFEC]">{attr}</span>
                          <Badge tone="brand">{r.confidence_type ?? "?"}</Badge>
                          <span className="font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                            {typeof r.confidence === "number" ? r.confidence.toFixed(2) : "?"}
                          </span>
                        </div>
                        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-[#e5e7eb] dark:bg-white/10">
                          <div
                            className="h-1.5 rounded-full bg-[#2fdebf]"
                            style={{ width: `${Math.round((r.confidence ?? 0) * 100)}%` }}
                          />
                        </div>
                        <p className="mt-2 font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]">
                          <span className="font-heading text-xs font-bold uppercase tracking-wide text-[#4a5058] dark:text-[#C3C2B7]">
                            Value ·{" "}
                          </span>
                          {String(r.value ?? "—")}
                        </p>
                        {r.evidence && (
                          <p className="mt-1 border-l-2 border-[#2fdebf] pl-2 font-sans text-sm italic text-[#4a5058] dark:text-[#C3C2B7]">
                            “{r.evidence}”
                          </p>
                        )}
                        <RatingBox runId={runId} agentName={agent?.name ?? agentId} attrName={attr} />
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
        </Card>
      )}
    </div>
  );
}
