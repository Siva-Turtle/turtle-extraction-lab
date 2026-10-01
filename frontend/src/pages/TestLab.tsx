import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Play, ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardTitle } from "../components/ui/Card";
import { ModelCombobox } from "../components/ui/Combobox";
import { fieldInput, fieldLabel, fieldTextarea } from "../components/ui/Modal";
import { PageHeader } from "../components/ui/PageHeader";
import type { Agent } from "./Agents";

type AttrResult = {
  value?: string | number | boolean | null;
  confidence?: number;
  confidence_type?: string;
  evidence?: string;
};

type RunOutputs = Record<string, Record<string, AttrResult> & { _error?: string }>;

type MeetingClient = { id: string; name: string };
type MeetingSummary = {
  id: string;
  title: string;
  date: string;
  duration_min: number;
  participants: string[];
};
type TranscriptResponse = { id: string; title: string; date: string; transcription: string };

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
  const [inputData, setInputData] = React.useState("");
  const [model, setModel] = React.useState("");
  const [modelsLive, setModelsLive] = React.useState(false);
  const [selected, setSelected] = React.useState<string[]>([]);
  const [runId, setRunId] = React.useState<string | null>(null);
  const [outputs, setOutputs] = React.useState<RunOutputs | null>(null);
  const [clientId, setClientId] = React.useState("");
  const [meetingTitle, setMeetingTitle] = React.useState("");
  const [meetingDate, setMeetingDate] = React.useState("");
  const [meetingId, setMeetingId] = React.useState("");

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
    queryFn: async () =>
      (await api.get("/meetings/titles", { params: { client_id: clientId } })).data as { titles: string[] },
    enabled: clientId !== "",
    staleTime: 60 * 1000,
    retry: false,
  });

  const meetingsQuery = useQuery({
    queryKey: ["meetings", clientId, meetingTitle, meetingDate],
    queryFn: async () => {
      const params: Record<string, string> = { client_id: clientId };
      if (meetingTitle) params.title = meetingTitle;
      if (meetingDate) params.date = meetingDate;
      return (await api.get("/meetings", { params })).data as { meetings: MeetingSummary[] };
    },
    enabled: clientId !== "",
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

  React.useEffect(() => {
    const t = transcriptQuery.data?.transcription;
    if (meetingId !== "" && typeof t === "string" && t !== "") setInputData(t);
  }, [transcriptQuery.data, meetingId]);

  function toggle(id: string) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  function handleClientChange(v: string) {
    setClientId(v);
    setMeetingTitle("");
    setMeetingId("");
    setInputData("");
  }

  function handleTitleChange(v: string) {
    setMeetingTitle(v);
    setMeetingId("");
    setInputData("");
  }

  function handleDateChange(v: string) {
    setMeetingDate(v);
    setMeetingId("");
    setInputData("");
  }

  function handleMeetingChange(v: string) {
    setMeetingId(v);
    setInputData("");
  }

  const run = useMutation({
    mutationFn: async () =>
      (await api.post("/runs", {
        input_type: "transcription",
        input_data: inputData,
        agent_ids: selected,
        model,
      })).data as { id: string; outputs: RunOutputs },
    onSuccess: (res) => {
      setRunId(res.id);
      setOutputs(res.outputs);
      toast.success("Run complete — rate each attribute below");
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Run failed"),
  });

  function canRun() {
    if (!inputData.trim()) return "Paste the input data first";
    if (!model.trim()) return "Enter an OpenRouter model id";
    if (selected.length === 0) return "Select at least one agent";
    return null;
  }

  const clientOptions: FilterOption[] = (clientsQuery.data ?? []).map((c) => ({
    value: c.id,
    label: c.name,
  }));
  const titleOptions: FilterOption[] = (titlesQuery.data?.titles ?? []).map((t) => ({
    value: t,
    label: t,
  }));
  const meetingOptions: FilterOption[] = (meetingsQuery.data?.meetings ?? []).map((m) => {
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
  const titleDisabled = clientId === "" || titlesQuery.isLoading || clientsDetail !== null || titlesDetail !== null;
  const dateDisabled = clientId === "" || clientsDetail !== null;
  const meetingDisabled =
    clientId === "" || meetingsQuery.isLoading || clientsDetail !== null || meetingsDetail !== null;
  const transcriptLoading = meetingId !== "" && transcriptQuery.isFetching;

  return (
    <div className="grid gap-4">
      <PageHeader title="Test Lab" description="Run agents over sample data, then rate every extracted attribute." />

      <Card>
        <div className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <span className={fieldLabel}>Input type</span>
              <input
                value="transcription"
                disabled
                aria-label="Input type (locked to transcription)"
                className={cn(fieldInput, "h-11 opacity-70")}
              />
              <p className="mt-1 font-sans text-xs text-[#8a8f98]">locked for now — transcription only</p>
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className={fieldLabel}>OpenRouter model</span>
                <KeyStatusBadge />
              </div>
              <ModelCombobox value={model} onChange={setModel} onLiveChange={setModelsLive} />
              <p className="mt-1 font-sans text-xs text-[#8a8f98]">
                {modelsLive
                  ? "Live list from OpenRouter."
                  : "Curated list — set OPENROUTER_API_KEY in backend/.env for the live catalogue. Any typed id still works."}
              </p>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <span className={fieldLabel}>Client</span>
              <FilterCombobox
                value={clientId}
                onChange={handleClientChange}
                options={clientOptions}
                placeholder={
                  clientsDetail !== null
                    ? "Unavailable — check backend"
                    : clientsQuery.isLoading
                      ? "Loading clients…"
                      : "Select client…"
                }
                disabled={clientDisabled}
                ariaLabel="Client"
              />
            </div>
            <div>
              <span className={fieldLabel}>Meeting title</span>
              <FilterCombobox
                value={meetingTitle}
                onChange={handleTitleChange}
                options={titleOptions}
                placeholder={
                  clientId === ""
                    ? "Select a client first"
                    : titlesDetail !== null
                      ? "Unavailable — check backend"
                      : titlesQuery.isLoading
                        ? "Loading titles…"
                        : "Select meeting title…"
                }
                disabled={titleDisabled}
                ariaLabel="Meeting title"
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
                clientId === ""
                  ? "Select a client first"
                  : meetingsDetail !== null
                    ? "Unavailable — check backend"
                    : meetingsQuery.isLoading
                      ? "Loading meetings…"
                      : "Select a meeting…"
              }
              disabled={meetingDisabled}
              ariaLabel="Meeting"
            />
            <p className="mt-1 font-sans text-xs text-[#8a8f98]">
              Selecting a meeting pulls its transcription below — the text stays editable.
            </p>
          </div>
          <label className={fieldLabel}>
            Input data (transcription — editable after pull)
            <textarea
              value={inputData}
              onChange={(e) => setInputData(e.target.value)}
              rows={6}
              disabled={transcriptLoading}
              placeholder={transcriptLoading ? "Loading transcript…" : "Select a meeting above to pull its transcription…"}
              className={cn(fieldTextarea, transcriptLoading && "opacity-70")}
            />
          </label>
          <div className={fieldLabel}>
            Agents to run
            {enabledAgents.length === 0 && (
              <p className="mt-1 font-sans text-sm font-normal normal-case tracking-normal text-[#b91c1c]">
                No agents yet — create one on the Agents tab.
              </p>
            )}
            <div className="mt-1.5 flex flex-wrap gap-2">
              {enabledAgents.map((a) => (
                <label
                  key={a.id}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-full border px-4 py-2 font-sans text-sm normal-case tracking-normal",
                    selected.includes(a.id)
                      ? "border-[#1d1d1d] bg-[#1d1d1d] text-white dark:border-[#2fdebf] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
                      : "border-[#e5e7eb] bg-white text-[#1d1d1d] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-transparent dark:text-[#F0EFEC]",
                  )}
                >
                  <input type="checkbox" checked={selected.includes(a.id)} onChange={() => toggle(a.id)} />
                  {a.name}
                </label>
              ))}
            </div>
          </div>
          <div>
            <Button
              loading={run.isPending}
              onClick={() => {
                const why = canRun();
                if (why) toast.error(why);
                else run.mutate();
              }}
            >
              <Play className="h-4 w-4" aria-hidden="true" /> {run.isPending ? "Running…" : "Run selected agents"}
            </Button>
          </div>
        </div>
      </Card>

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
