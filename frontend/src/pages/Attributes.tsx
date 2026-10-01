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

export type AttributeType = "string" | "number" | "boolean" | "enum";

export type LabAttribute = {
  id: string;
  agent_ids: string[];
  name: string;
  type: AttributeType;
  description: string;
  enum_values: string[];
};

const TYPES: AttributeType[] = ["string", "number", "boolean", "enum"];
const EMPTY: {
  agent_ids: string[];
  name: string;
  type: AttributeType;
  description: string;
  enum_values: string[];
} = { agent_ids: [], name: "", type: "string", description: "", enum_values: [] };

/** One value per line or comma-separated — split on newlines+commas, trim, drop empties. */
function parseEnumValues(raw: string): string[] {
  return raw
    .split(/[\n,]+/)
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

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
      const body = {
        agent_ids: v.agent_ids,
        name: v.name,
        type: v.type,
        description: v.description,
        enum_values: v.enum_values,
      };
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
      toast.error("Create an agent first — every attribute maps to one or more agents");
      return;
    }
    const seed =
      agentFilter && agents.some((a) => a.id === agentFilter) ? agentFilter : agents[0].id;
    setEditing({ ...EMPTY, agent_ids: [seed] });
  }

  function toggleAgent(id: string) {
    if (!editing) return;
    const has = editing.agent_ids.includes(id);
    setEditing({
      ...editing,
      agent_ids: has ? editing.agent_ids.filter((x) => x !== id) : [...editing.agent_ids, id],
    });
  }

  function agentNames(r: LabAttribute): string {
    const names = (r.agent_ids ?? []).map(
      (id) => agents.find((a) => a.id === id)?.name ?? id.slice(0, 8),
    );
    return names.length > 0 ? names.join(", ") : "—";
  }

  function trySave() {
    if (!editing) return;
    if (editing.agent_ids.length === 0) {
      toast.error("Pick at least one agent");
      return;
    }
    if (!editing.name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (editing.type === "enum" && editing.enum_values.length === 0) {
      toast.error("Enum needs at least one value");
      return;
    }
    save.mutate(editing);
  }

  return (
    <div className="grid gap-4">
      <PageHeader
        title="Attributes"
        description="Type, description, allowed values — each mapped to one or more agents. Every agent returns the same shape per attribute: value · confidence · confidence_type · evidence (missing attributes are omitted, never null)."
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
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            No attributes here yet — map the first one to one or more agents.
          </p>
        </Card>
      ) : (
        <Card padded={false} className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-[#e5e7eb] font-heading text-xs font-bold uppercase tracking-wide text-[#8a8f98] dark:border-white/10">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Agents</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Values</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id} className="border-b border-[#e5e7eb] last:border-0 hover:bg-[#e8fbf6]/50 dark:border-white/10 dark:hover:bg-white/5">
                  <td className="px-4 py-3 font-heading font-semibold text-[#1d1d1d] dark:text-[#F0EFEC]">{r.name}</td>
                  <td className="px-4 py-3 text-[#4a5058] dark:text-[#C3C2B7]">
                    {agentNames(r)}
                  </td>
                  <td className="px-4 py-3"><Badge tone="neutral">{r.type}</Badge></td>
                  <td className="px-4 py-3">
                    {r.type === "enum" && (r.enum_values ?? []).length > 0 ? (
                      <span className="flex flex-wrap gap-1">
                        {(r.enum_values ?? []).map((v) => (
                          <Badge key={v} tone="neutral">{v}</Badge>
                        ))}
                      </span>
                    ) : (
                      <span className="font-heading text-xs text-[#8a8f98]">—</span>
                    )}
                  </td>
                  <td className="max-w-64 truncate px-4 py-3 text-[#4a5058] dark:text-[#C3C2B7]" title={r.description}>
                    {r.description || "—"}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          setEditing({
                            ...r,
                            agent_ids: [...(r.agent_ids ?? [])],
                            enum_values: [...(r.enum_values ?? [])],
                          })
                        }
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
          <div className={fieldLabel}>
            Agents <span aria-hidden="true" className="text-[#ef4444]"> *</span>
            {agents.length === 0 ? (
              <p className="mt-1 font-sans text-sm font-normal normal-case tracking-normal text-[#b91c1c]">
                No agents yet — create one on the Agents tab.
              </p>
            ) : (
              <div className="mt-1.5 flex flex-wrap gap-2">
                {agents.map((a) => (
                  <label
                    key={a.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-2 rounded-full border px-4 py-2 font-sans text-sm normal-case tracking-normal",
                      editing.agent_ids.includes(a.id)
                        ? "border-[#1d1d1d] bg-[#1d1d1d] text-white dark:border-[#2fdebf] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
                        : "border-[#e5e7eb] bg-white text-[#1d1d1d] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-transparent dark:text-[#F0EFEC]",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={editing.agent_ids.includes(a.id)}
                      onChange={() => toggleAgent(a.id)}
                    />
                    {a.name}
                  </label>
                ))}
              </div>
            )}
          </div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
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
                onChange={(e) => {
                  const next = e.target.value as AttributeType;
                  setEditing({
                    ...editing,
                    type: next,
                    enum_values: next === "enum" ? editing.enum_values : [],
                  });
                }}
                className={cn(fieldInput, "h-11")}
              >
                {TYPES.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
            </label>
          </div>
          <label className={cn(fieldLabel, "mt-3 block")}>
            Description
            <textarea
              value={editing.description}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              rows={2}
              className={fieldTextarea}
            />
          </label>
          {editing.type === "enum" && (
            <label className={cn(fieldLabel, "mt-3 block")}>
              Allowed values <span aria-hidden="true" className="text-[#ef4444]"> *</span>
              <textarea
                value={editing.enum_values.join("\n")}
                onChange={(e) => setEditing({ ...editing, enum_values: parseEnumValues(e.target.value) })}
                rows={3}
                spellCheck={false}
                placeholder={"One value per line, or comma-separated"}
                className={cn(fieldTextarea, "font-mono text-xs")}
              />
            </label>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button loading={save.isPending} onClick={trySave}>
              Save
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
