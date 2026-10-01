import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import type { Agent } from "./Agents";

type AttrResult = {
  value?: string | number | boolean | null;
  confidence?: number;
  confidence_type?: string;
  evidence?: string;
};

type RunOutputs = Record<string, Record<string, AttrResult> & { _error?: string }>;

const inputCls = "mt-1 w-full rounded-lg border border-ink-200 p-2 font-sans text-sm";
const btnPrimary = "rounded-lg bg-brand px-4 py-2 font-heading text-sm font-semibold text-ink";

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

  if (done) return <p className="mt-2 font-heading text-xs text-success">Rated {rating === "up" ? "👍" : "👎"} — saved.</p>;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <button
        onClick={() => setRating("up")}
        title="Thumbs up"
        className={`rounded-lg border p-1.5 ${rating === "up" ? "border-success bg-success/10 text-success" : "border-ink-200"}`}
      >
        <ThumbsUp className="h-4 w-4" />
      </button>
      <button
        onClick={() => setRating("down")}
        title="Thumbs down"
        className={`rounded-lg border p-1.5 ${rating === "down" ? "border-danger bg-danger/10 text-danger" : "border-ink-200"}`}
      >
        <ThumbsDown className="h-4 w-4" />
      </button>
      <input
        value={remarks}
        onChange={(e) => setRemarks(e.target.value)}
        placeholder="Remarks for this attribute…"
        className="min-w-40 flex-1 rounded-lg border border-ink-200 p-1.5 text-sm"
      />
      <button
        onClick={() => (rating ? save.mutate() : toast.error("Pick 👍 or 👎 first"))}
        disabled={save.isPending}
        className="rounded-lg border border-ink-200 px-3 py-1.5 font-heading text-xs"
      >
        {save.isPending ? "Saving…" : "Save rating"}
      </button>
    </div>
  );
}

export default function TestLab() {
  const [inputType, setInputType] = useState("transcription");
  const [inputData, setInputData] = useState("");
  const [model, setModel] = useState("");
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
      <section className="rounded-xl border border-ink-100 bg-surface p-5 shadow-float">
        <h1 className="font-module text-lg">Test Lab</h1>
        <div className="mt-4 grid gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="font-heading text-xs text-ink-500">
              Input type
              <select value={inputType} onChange={(e) => setInputType(e.target.value)} className={inputCls}>
                <option value="transcription">transcription</option>
                <option value="messages">messages</option>
                <option value="mail">mail</option>
              </select>
            </label>
            <label className="font-heading text-xs text-ink-500">
              OpenRouter model
              <input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="e.g. anthropic/claude-sonnet-4"
                className={inputCls}
              />
            </label>
          </div>
          <label className="font-heading text-xs text-ink-500">
            Input data (exact transcription / message / mail)
            <textarea value={inputData} onChange={(e) => setInputData(e.target.value)} rows={6} className={inputCls} />
          </label>
          <div className="font-heading text-xs text-ink-500">
            Agents to run
            {agents.length === 0 && <p className="mt-1 text-danger">No agents yet — create one on the Agents tab.</p>}
            <div className="mt-1 flex flex-wrap gap-2">
              {agents.map((a) => (
                <label key={a.id} className={`flex cursor-pointer items-center gap-1 rounded-lg border px-3 py-2 text-sm ${selected.includes(a.id) ? "border-brand bg-brand-50" : "border-ink-200"}`}>
                  <input type="checkbox" checked={selected.includes(a.id)} onChange={() => toggle(a.id)} />
                  {a.name}
                </label>
              ))}
            </div>
          </div>
          <div>
            <button
              onClick={() => {
                const why = canRun();
                if (why) toast.error(why);
                else run.mutate();
              }}
              disabled={run.isPending}
              className={btnPrimary}
            >
              <Play className="mr-1 inline h-4 w-4" /> {run.isPending ? "Running…" : "Run selected agents"}
            </button>
          </div>
        </div>
      </section>

      {outputs && runId && (
        <section className="rounded-xl border border-ink-100 bg-surface p-5 shadow-float">
          <h2 className="font-module text-base">Results <span className="font-heading text-xs font-normal text-ink-400">run {runId.slice(0, 8)}</span></h2>
          <div className="mt-3 grid gap-4">
            {Object.entries(outputs).map(([agentId, out]) => {
              const agent = agents.find((a) => a.id === agentId);
              if (out._error) {
                return (
                  <div key={agentId} className="rounded-xl border border-danger/40 bg-danger/5 p-4">
                    <h3 className="font-heading text-sm font-semibold">{agent?.name ?? agentId}</h3>
                    <p className="mt-1 text-sm text-danger">Agent failed: {out._error}</p>
                  </div>
                );
              }
              return (
                <div key={agentId} className="rounded-xl border border-ink-100 p-4">
                  <h3 className="font-heading text-sm font-semibold">{agent?.name ?? agentId}</h3>
                  <div className="mt-2 grid gap-2">
                    {(Object.entries(out) as [string, AttrResult][]).filter(([k]) => k !== "_error").map(([attr, r]) => (
                      <div key={attr} className="rounded-lg bg-ink-50 p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-heading text-sm font-semibold">{attr}</span>
                          <span className="rounded-full bg-brand-100 px-2 py-0.5 font-heading text-[11px]">
                            {r.confidence_type ?? "?"}
                          </span>
                          <span className="font-heading text-xs text-ink-500">
                            confidence {typeof r.confidence === "number" ? r.confidence.toFixed(2) : "?"}
                          </span>
                        </div>
                        <div className="mt-1 h-1.5 w-full rounded-full bg-ink-100">
                          <div
                            className="h-1.5 rounded-full bg-brand"
                            style={{ width: `${Math.round((r.confidence ?? 0) * 100)}%` }}
                          />
                        </div>
                        <p className="mt-1 text-sm"><span className="font-heading text-ink-500">Value: </span>{String(r.value ?? "—")}</p>
                        {r.evidence && (
                          <p className="mt-1 border-l-2 border-brand pl-2 text-sm italic text-ink-500">“{r.evidence}”</p>
                        )}
                        <RatingBox runId={runId} agentName={agent?.name ?? agentId} attrName={attr} />
                      </div>
                    ))}
                    {Object.keys(out).length === 0 && (
                      <p className="font-heading text-xs text-ink-400">Agent returned no attributes.</p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
