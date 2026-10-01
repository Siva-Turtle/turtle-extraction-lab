import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import type { Agent } from "./Agents";

export type LabAttribute = {
  id: string;
  agent_id: string;
  name: string;
  type: string;
  description: string;
  json_schema: Record<string, unknown>;
  required: boolean;
};

const TYPES = ["string", "number", "integer", "boolean", "date", "datetime", "enum", "list"];
const EMPTY = { agent_id: "", name: "", type: "string", description: "", json_schema: "{}", required: false };

const inputCls = "mt-1 w-full rounded-lg border border-ink-200 p-2 font-sans text-sm";
const btnPrimary = "rounded-lg bg-brand px-4 py-2 font-heading text-sm font-semibold text-ink";

export default function Attributes() {
  const qc = useQueryClient();
  const [agentFilter, setAgentFilter] = useState("");
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as Agent[],
  });
  const { data = [], isLoading } = useQuery({
    queryKey: ["attributes", agentFilter],
    queryFn: async () =>
      (await api.get("/attributes", { params: agentFilter ? { agent_id: agentFilter } : {} }))
        .data as LabAttribute[],
  });
  const [editing, setEditing] = useState<(typeof EMPTY & { id?: string }) | null>(null);

  const save = useMutation({
    mutationFn: async (v: typeof EMPTY & { id?: string }) => {
      let schema: Record<string, unknown> = {};
      try {
        schema = v.json_schema.trim() ? JSON.parse(v.json_schema) : {};
      } catch {
        throw new Error("json_schema is not valid JSON");
      }
      const body = { ...v, json_schema: schema };
      return v.id
        ? (await api.patch(`/attributes/${v.id}`, body)).data
        : (await api.post("/attributes", body)).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["attributes"] });
      setEditing(null);
      toast.success("Attribute saved");
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Save failed"),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/attributes/${id}`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["attributes"] });
      toast.success("Attribute deleted");
    },
    onError: () => toast.error("Delete failed"),
  });

  function startNew() {
    if (agents.length === 0) {
      toast.error("Create an agent first — every attribute maps to one agent");
      return;
    }
    setEditing({ ...EMPTY, agent_id: agentFilter || agents[0].id });
  }

  return (
    <section className="rounded-xl border border-ink-100 bg-surface p-5 shadow-float">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-module text-lg">Attributes</h1>
          <p className="mt-1 font-heading text-xs text-ink-400">
            Type, description, structured-output schema — each mapped to one agent.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="rounded-lg border border-ink-200 p-2 font-heading text-sm"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>
          <button onClick={startNew} className={btnPrimary}>
            <Plus className="mr-1 inline h-4 w-4" /> New attribute
          </button>
        </div>
      </div>

      {isLoading ? (
        <p className="mt-4 font-heading text-sm text-ink-400">Loading…</p>
      ) : data.length === 0 ? (
        <p className="mt-4 rounded-lg bg-ink-50 p-4 font-heading text-sm text-ink-500">
          No attributes here yet.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="font-heading text-xs text-ink-400">
                <th className="p-2">Name</th>
                <th className="p-2">Agent</th>
                <th className="p-2">Type</th>
                <th className="p-2">Required</th>
                <th className="p-2">Description</th>
                <th className="p-2"></th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id} className="border-t border-ink-100">
                  <td className="p-2 font-heading font-semibold">{r.name}</td>
                  <td className="p-2">{agents.find((a) => a.id === r.agent_id)?.name ?? r.agent_id.slice(0, 8)}</td>
                  <td className="p-2"><span className="rounded-full bg-ink-50 px-2 py-0.5 font-heading text-[11px]">{r.type}</span></td>
                  <td className="p-2">{r.required ? "yes" : "no"}</td>
                  <td className="p-2 text-ink-500">{r.description}</td>
                  <td className="p-2">
                    <div className="flex gap-1">
                      <button
                        onClick={() => setEditing({ ...r, json_schema: JSON.stringify(r.json_schema ?? {}, null, 2) })}
                        className="rounded-lg border border-ink-200 p-1.5"
                        title="Edit"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        onClick={() => window.confirm(`Delete attribute "${r.name}"?`) && remove.mutate(r.id)}
                        className="rounded-lg border border-ink-200 p-1.5 text-danger"
                        title="Delete"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 z-20 flex items-end justify-center bg-ink/40 p-0 sm:items-center sm:p-6">
          <div className="max-h-[90vh] w-full max-w-2xl overflow-auto rounded-t-2xl bg-surface p-5 sm:rounded-2xl">
            <h2 className="font-module text-base">{editing.id ? "Edit attribute" : "New attribute"}</h2>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="font-heading text-xs text-ink-500">
                Agent
                <select
                  value={editing.agent_id}
                  onChange={(e) => setEditing({ ...editing, agent_id: e.target.value })}
                  className={inputCls}
                >
                  {agents.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
              </label>
              <label className="font-heading text-xs text-ink-500">
                Name
                <input
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  className={inputCls}
                />
              </label>
              <label className="font-heading text-xs text-ink-500">
                Type
                <select
                  value={editing.type}
                  onChange={(e) => setEditing({ ...editing, type: e.target.value })}
                  className={inputCls}
                >
                  {TYPES.map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 font-heading text-xs text-ink-500">
                <input
                  type="checkbox"
                  checked={editing.required}
                  onChange={(e) => setEditing({ ...editing, required: e.target.checked })}
                />
                Required
              </label>
            </div>
            <label className="mt-3 block font-heading text-xs text-ink-500">
              Description (goes into the agent prompt)
              <textarea
                value={editing.description}
                onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                rows={2}
                className={inputCls}
              />
            </label>
            <label className="mt-3 block font-heading text-xs text-ink-500">
              Structured-output schema (JSON)
              <textarea
                value={editing.json_schema}
                onChange={(e) => setEditing({ ...editing, json_schema: e.target.value })}
                rows={3}
                spellCheck={false}
                className={`${inputCls} font-mono`}
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setEditing(null)} className="rounded-lg border border-ink-200 px-4 py-2 font-heading text-sm">
                Cancel
              </button>
              <button
                onClick={() => editing.name.trim() && editing.agent_id ? save.mutate(editing) : toast.error("Agent + name are required")}
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
