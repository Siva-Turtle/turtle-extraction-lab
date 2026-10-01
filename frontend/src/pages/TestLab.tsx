import { useState } from "react";
import { toast } from "sonner";
import { api } from "../lib/api";

export default function TestLab() {
  const [inputType, setInputType] = useState("transcription");
  const [inputData, setInputData] = useState("");
  const [model, setModel] = useState("");
  const [output, setOutput] = useState<unknown>(null);

  async function run() {
    try {
      const agents = (await api.get("/agents")).data as { id: string }[];
      const res = await api.post("/runs", {
        input_type: inputType,
        input_data: inputData,
        agent_ids: agents.map((a) => a.id),
        model,
      });
      setOutput(res.data.outputs);
      toast.success("Run complete — rate each attribute below");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Run failed");
    }
  }

  return (
    <section className="rounded-xl border border-ink-100 bg-surface p-5 shadow-float">
      <h1 className="font-module text-lg">Test Lab</h1>
      <div className="mt-4 grid gap-3">
        <label className="font-heading text-xs text-ink-500">
          Input type
          <select value={inputType} onChange={(e) => setInputType(e.target.value)} className="mt-1 w-full rounded-lg border border-ink-200 p-2">
            <option value="transcription">transcription</option>
            <option value="messages">messages</option>
            <option value="mail">mail</option>
          </select>
        </label>
        <label className="font-heading text-xs text-ink-500">
          Input data
          <textarea value={inputData} onChange={(e) => setInputData(e.target.value)} rows={6} className="mt-1 w-full rounded-lg border border-ink-200 p-2 font-sans" />
        </label>
        <label className="font-heading text-xs text-ink-500">
          OpenRouter model
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. anthropic/claude-sonnet-4" className="mt-1 w-full rounded-lg border border-ink-200 p-2" />
        </label>
        <button onClick={run} className="rounded-lg bg-brand px-4 py-2 font-heading text-sm font-semibold text-ink">
          Run selected agents
        </button>
        <pre className="overflow-auto rounded-lg bg-ink-50 p-3 text-xs">{JSON.stringify(output ?? {}, null, 2)}</pre>
      </div>
    </section>
  );
}
