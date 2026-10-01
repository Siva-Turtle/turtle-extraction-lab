import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Modal, fieldInput, fieldLabel, fieldTextarea } from "../components/ui/Modal";
import { PageHeader } from "../components/ui/PageHeader";
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
    <div className="grid gap-4">
      <PageHeader
        title="Attributes"
        description="Type, description, structured-output schema — each mapped to one agent."
        actions={
          <>
            <select
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              aria-label="Filter by agent"
              className="h-11 rounded-full border border-[#e5e7eb] bg-white px-4 font-heading text-sm font-semibold text-[#1d1d1d] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-transparent dark:text-[#F0EFEC] dark:hover:border-white/40"
            >
              <option value="">All agents</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
            <Button size="sm" onClick={startNew}>
              <Plus className="h-4 w-4" aria-hidden="true" /> New attribute
            </Button>
          </>
        }
      />

      {isLoading ? (
        <Card>
          <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
        </Card>
      ) : data.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">No attributes here yet.</p>
        </Card>
      ) : (
        <Card padded={false} className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-[#e5e7eb] font-heading text-xs font-bold uppercase tracking-wide text-[#8a8f98] dark:border-white/10">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Agent</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Required</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id} className="border-b border-[#e5e7eb] last:border-0 hover:bg-[#e8fbf6]/50 dark:border-white/10 dark:hover:bg-white/5">
                  <td className="px-4 py-3 font-heading font-semibold text-[#1d1d1d] dark:text-[#F0EFEC]">{r.name}</td>
                  <td className="px-4 py-3 text-[#4a5058] dark:text-[#C3C2B7]">
                    {agents.find((a) => a.id === r.agent_id)?.name ?? r.agent_id.slice(0, 8)}
                  </td>
                  <td className="px-4 py-3"><Badge tone="neutral">{r.type}</Badge></td>
                  <td className="px-4 py-3">
                    {r.required ? <Badge tone="warning">required</Badge> : <span className="font-heading text-xs text-[#8a8f98]">optional</span>}
                  </td>
                  <td className="max-w-64 truncate px-4 py-3 text-[#4a5058] dark:text-[#C3C2B7]" title={r.description}>
                    {r.description || "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditing({ ...r, json_schema: JSON.stringify(r.json_schema ?? {}, null, 2) })}
                        aria-label={`Edit ${r.name}`}
                      >
                        <Pencil className="h-4 w-4" aria-hidden="true" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => window.confirm(`Delete attribute "${r.name}"?`) && remove.mutate(r.id)}
                        aria-label={`Delete ${r.name}`}
                        className="text-[#ef4444]"
                      >
                        <Trash2 className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {editing && (
        <Modal title={editing.id ? "Edit attribute" : "New attribute"} onClose={() => setEditing(null)}>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={fieldLabel}>
              Agent
              <select
                value={editing.agent_id}
                onChange={(e) => setEditing({ ...editing, agent_id: e.target.value })}
                className={cn(fieldInput, "h-11")}
              >
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label className={fieldLabel}>
              Name <span aria-hidden="true" className="text-[#ef4444]"> *</span>
              <input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className={fieldInput}
              />
            </label>
            <label className={fieldLabel}>
              Type
              <select
                value={editing.type}
                onChange={(e) => setEditing({ ...editing, type: e.target.value })}
                className={cn(fieldInput, "h-11")}
              >
                {TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]">
              <input
                type="checkbox"
                checked={editing.required}
                onChange={(e) => setEditing({ ...editing, required: e.target.checked })}
                className="h-5 w-5 accent-[#0d5c4a]"
              />
              Required attribute
            </label>
          </div>
          <label className={cn(fieldLabel, "mt-3 block")}>
            Description (goes into the agent prompt)
            <textarea
              value={editing.description}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              rows={2}
              className={fieldTextarea}
            />
          </label>
          <label className={cn(fieldLabel, "mt-3 block")}>
            Structured-output schema (JSON)
            <textarea
              value={editing.json_schema}
              onChange={(e) => setEditing({ ...editing, json_schema: e.target.value })}
              rows={3}
              spellCheck={false}
              className={cn(fieldTextarea, "font-mono text-xs")}
            />
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button
              loading={save.isPending}
              onClick={() => editing.name.trim() && editing.agent_id ? save.mutate(editing) : toast.error("Agent + name are required")}
            >
              Save
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
