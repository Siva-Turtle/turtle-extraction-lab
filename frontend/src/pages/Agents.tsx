import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardTitle } from "../components/ui/Card";
import { Modal, fieldInput, fieldLabel, fieldTextarea } from "../components/ui/Modal";
import { PageHeader } from "../components/ui/PageHeader";

export type Agent = {
  id: string;
  name: string;
  system_instruction: string;
  prompt: string;
  input_types: string[];
};

const INPUT_TYPES = ["transcription", "messages", "mail"];
const EMPTY = { name: "", system_instruction: "", prompt: "", input_types: [] as string[] };

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
    <div className="grid gap-4">
      <PageHeader
        title="Agents"
        description="Define system instruction + prompt per agent. Deleting an agent deletes its attributes."
        actions={
          <Button size="sm" onClick={() => setEditing({ ...EMPTY })}>
            <Plus className="h-4 w-4" aria-hidden="true" /> New agent
          </Button>
        }
      />

      {isLoading ? (
        <Card>
          <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
        </Card>
      ) : data.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            No agents yet — create the first one to start testing.
          </p>
        </Card>
      ) : (
        <div className="grid gap-3">
          {data.map((a) => (
            <Card key={a.id} padded={false} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle>{a.name}</CardTitle>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {a.input_types.map((t) => (
                      <Badge key={t} tone="brand">{t}</Badge>
                    ))}
                  </div>
                </div>
                <div className="flex shrink-0 gap-2">
                  <Button variant="secondary" size="sm" onClick={() => setEditing({ ...a })} aria-label={`Edit ${a.name}`}>
                    <Pencil className="h-4 w-4" aria-hidden="true" />
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => window.confirm(`Delete agent "${a.name}" and its attributes?`) && remove.mutate(a.id)}
                    aria-label={`Delete ${a.name}`}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </div>
              </div>
              <details className="mt-3">
                <summary className="cursor-pointer font-heading text-xs font-semibold text-[#4a5058] dark:text-[#C3C2B7]">
                  System instruction + prompt
                </summary>
                <pre className="mt-2 whitespace-pre-wrap rounded-xl bg-[#f1f2f3] p-3 font-sans text-xs text-[#1d1d1d] dark:bg-white/5 dark:text-[#F0EFEC]">
                  {a.system_instruction || "—"}
                </pre>
                <pre className="mt-2 whitespace-pre-wrap rounded-xl bg-[#f1f2f3] p-3 font-sans text-xs text-[#1d1d1d] dark:bg-white/5 dark:text-[#F0EFEC]">
                  {a.prompt || "—"}
                </pre>
              </details>
            </Card>
          ))}
        </div>
      )}

      {editing && (
        <Modal title={editing.id ? "Edit agent" : "New agent"} onClose={() => setEditing(null)}>
          <label className={fieldLabel}>
            Name <span aria-hidden="true" className="text-[#ef4444]"> *</span>
            <input
              value={editing.name}
              onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              className={fieldInput}
            />
          </label>
          <label className={cn(fieldLabel, "mt-3 block")}>
            System instruction
            <textarea
              value={editing.system_instruction}
              onChange={(e) => setEditing({ ...editing, system_instruction: e.target.value })}
              rows={3}
              className={fieldTextarea}
            />
          </label>
          <label className={cn(fieldLabel, "mt-3 block")}>
            Prompt
            <textarea
              value={editing.prompt}
              onChange={(e) => setEditing({ ...editing, prompt: e.target.value })}
              rows={4}
              className={fieldTextarea}
            />
          </label>
          <div className={cn(fieldLabel, "mt-3")}>
            Input types
            <div className="mt-1.5 flex flex-wrap gap-2">
              {INPUT_TYPES.map((t) => (
                <label
                  key={t}
                  className={cn(
                    "flex cursor-pointer items-center gap-2 rounded-full border px-4 py-2 font-sans text-sm normal-case tracking-normal",
                    editing.input_types.includes(t)
                      ? "border-[#1d1d1d] bg-[#1d1d1d] text-white dark:border-[#2fdebf] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
                      : "border-[#e5e7eb] bg-white text-[#1d1d1d] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-transparent dark:text-[#F0EFEC]",
                  )}
                >
                  <input type="checkbox" checked={editing.input_types.includes(t)} onChange={() => toggleType(t)} />
                  {t}
                </label>
              ))}
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button
              loading={save.isPending}
              onClick={() => editing.name.trim() ? save.mutate(editing) : toast.error("Name is required")}
            >
              Save
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
