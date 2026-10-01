import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";

export type Agent = {
  id: string;
  name: string;
  system_instruction: string;
  prompt: string;
  input_types: string[];
};

const INPUT_TYPES = ["transcription", "messages", "mail"];
const EMPTY = { name: "", system_instruction: "", prompt: "", input_types: [] as string[] };

const inputCls = "mt-1 w-full rounded-lg border border-ink-200 p-2 font-sans text-sm";
const btnPrimary = "rounded-lg bg-brand px-4 py-2 font-heading text-sm font-semibold text-ink";

export default function Agents() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as Agent[],
  });
  const [editing, setEditing] = useState<(typeof EMPTY & { id?: string }) | null>(null);

  const save = useMutation({
    mutationFn: async (v: typeof EMPTY & { id?: string }) =>
      v.id
        ? (await api.patch(`/agents/${v.id}`, v)).data
        : (await api.post("/agents", v)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      setEditing(null);
      toast.success("Agent saved");
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Save failed"),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/agents/${id}`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents", "attributes"] });
      toast.success("Agent deleted (its attributes went with it)");
    },
    onError: () => toast.error("Delete failed"),
  });

  function toggleType(t: string) {
    if (!editing) return;
    const cur = editing.input_types.includes(t)
      ? editing.input_types.filter((x) => x !== t)
      : [...editing.input_types, t];
    setEditing({ ...editing, input_types: cur });
  }

  return (
    <section className="rounded-xl border border-ink-100 bg-surface p-5 shadow-float">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-module text-lg">Agents</h1>
          <p className="mt-1 font-heading text-xs text-ink-400">
            Define system instruction + prompt per agent. Deleting an agent deletes its attributes.
          </p>
        </div>
        <button onClick={() => setEditing({ ...EMPTY })} className={btnPrimary}>
          <Plus className="mr-1 inline h-4 w-4" /> New agent
        </button>
      </div>

      {isLoading ? (
        <p className="mt-4 font-heading text-sm text-ink-400">Loading…</p>
      ) : data.length === 0 ? (
        <p className="mt-4 rounded-lg bg-ink-50 p-4 font-heading text-sm text-ink-500">
          No agents yet — create the first one to start testing.
        </p>
      ) : (
        <div className="mt-4 grid gap-3">
          {data.map((a) => (
            <div key={a.id} className="rounded-xl border border-ink-100 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-heading text-sm font-semibold">{a.name}</h2>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {a.input_types.map((t) => (
                      <span key={t} className="rounded-full bg-brand-100 px-2 py-0.5 font-heading text-[11px]">
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => setEditing({ ...a })}
                    className="rounded-lg border border-ink-200 p-2"
                    title="Edit"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => window.confirm(`Delete agent "${a.name}" and its attributes?`) && remove.mutate(a.id)}
                    className="rounded-lg border border-ink-200 p-2 text-danger"
                    title="Delete"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
              <details className="mt-2 text-xs">
                <summary className="cursor-pointer font-heading text-ink-500">System instruction + prompt</summary>
                <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-ink-50 p-3">{a.system_instruction}</pre>
                <pre className="mt-2 whitespace-pre-wrap rounded-lg bg-ink-50 p-3">{a.prompt}</pre>
              </details>
            </div>
          ))}
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 z-20 flex items-end justify-center bg-ink/40 p-0 sm:items-center sm:p-6">
          <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-t-2xl bg-surface p-5 sm:rounded-2xl">
            <h2 className="font-module text-base">{editing.id ? "Edit agent" : "New agent"}</h2>
            <label className="mt-3 block font-heading text-xs text-ink-500">
              Name
              <input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className={inputCls}
              />
            </label>
            <label className="mt-3 block font-heading text-xs text-ink-500">
              System instruction
              <textarea
                value={editing.system_instruction}
                onChange={(e) => setEditing({ ...editing, system_instruction: e.target.value })}
                rows={3}
                className={inputCls}
              />
            </label>
            <label className="mt-3 block font-heading text-xs text-ink-500">
              Prompt
              <textarea
                value={editing.prompt}
                onChange={(e) => setEditing({ ...editing, prompt: e.target.value })}
                rows={4}
                className={inputCls}
              />
            </label>
            <div className="mt-3 font-heading text-xs text-ink-500">
              Input types
              <div className="mt-1 flex gap-2">
                {INPUT_TYPES.map((t) => (
                  <label key={t} className="flex items-center gap-1 rounded-lg border border-ink-200 px-3 py-2 text-sm">
                    <input type="checkbox" checked={editing.input_types.includes(t)} onChange={() => toggleType(t)} />
                    {t}
                  </label>
                ))}
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setEditing(null)} className="rounded-lg border border-ink-200 px-4 py-2 font-heading text-sm">
                Cancel
              </button>
              <button
                onClick={() => editing.name.trim() ? save.mutate(editing) : toast.error("Name is required")}
                disabled={save.isPending}
                className={btnPrimary}
              >
                {save.isPending ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
