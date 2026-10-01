import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, ThumbsDown, ThumbsUp } from "lucide-react";
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

function RatingBox({ runId, agentName, attrName }: { runId: string; agentName: string; attrName: string }) {
  const qc = useQueryClient();
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [remarks, setRemarks] = useState("");
  const [done, setDone] = useState(false);

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
  const [inputType, setInputType] = useState("transcription");
  const [inputData, setInputData] = useState("");
  const [model, setModel] = useState("");
  const [modelsLive, setModelsLive] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [outputs, setOutputs] = useState<RunOutputs | null>(null);

  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as Agent[],
  });

  function toggle(id: string) {
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  }

  const run = useMutation({
    mutationFn: async () =>
      (await api.post("/runs", {
        input_type: inputType,
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

  return (
    <div className="grid gap-4">
      <PageHeader title="Test Lab" description="Run agents over sample data, then rate every extracted attribute." />

      <Card>
        <div className="grid gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className={fieldLabel}>
              Input type
              <select value={inputType} onChange={(e) => setInputType(e.target.value)} className={cn(fieldInput, "h-11")}>
                <option value="transcription">transcription</option>
                <option value="messages">messages</option>
                <option value="mail">mail</option>
              </select>
            </label>
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
          <label className={fieldLabel}>
            Input data (exact transcription / message / mail)
            <textarea value={inputData} onChange={(e) => setInputData(e.target.value)} rows={6} className={fieldTextarea} />
          </label>
          <div className={fieldLabel}>
            Agents to run
            {agents.length === 0 && (
              <p className="mt-1 font-sans text-sm font-normal normal-case tracking-normal text-[#b91c1c]">
                No agents yet — create one on the Agents tab.
              </p>
            )}
            <div className="mt-1.5 flex flex-wrap gap-2">
              {agents.map((a) => (
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
