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
  input_types: string[];
  is_enabled: boolean;
};

const INPUT_TYPES = ["transcription", "messages", "mail"];
export const DEFAULT_SYSTEM_INSTRUCTION = `Extract only information stated in the input transcription.\n\nReturn a JSON object keyed by attribute name. Each value has "value", "confidence" (0-1), "confidence_type" (quoted|inferred|normalized), "evidence" (exact quote). Omit attributes not found — never return null. quoted = stated word-for-word; inferred = concluded but not stated verbatim; normalized = standardized from a stated form (phone digits, dates, casing).\n\nThe attribute list is attached automatically; the transcription arrives as the input message.`;
const EMPTY = { name: "", system_instruction: DEFAULT_SYSTEM_INSTRUCTION, input_types: [] as string[], is_enabled: true };

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
      toast.success("Agent deleted (shared attributes stay with their other agents)");
    },
    onError: () => toast.error("Delete failed"),
  });

  const toggleEnabled = useMutation({
    mutationFn: async ({ id, is_enabled }: { id: string; is_enabled: boolean }) =>
      (await api.patch(`/agents/${id}`, { is_enabled })).data as Agent,
    onMutate: async ({ id, is_enabled }) => {
      await qc.cancelQueries({ queryKey: ["agents"] });
      const prev = qc.getQueryData<Agent[]>(["agents"]);
      qc.setQueryData<Agent[]>(["agents"], (old) =>
        (old ?? []).map((a) => (a.id === id ? { ...a, is_enabled } : a)),
      );
      return { prev };
    },
    onError: (e: unknown, _vars, context) => {
      if (context?.prev) qc.setQueryData(["agents"], context.prev);
      const detail =
        typeof (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail === "string"
          ? ((e as { response: { data: { detail: string } } }).response.data.detail as string)
          : "Could not update agent status";
      toast.error(detail);
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
    onSuccess: (_data, vars) => {
      toast.success(vars.is_enabled ? "Agent enabled" : "Agent disabled");
    },
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
        description="Define the system instruction per agent. Runs use scrubbed transcription. Attributes can be shared across agents."
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
            <Card key={a.id} padded={false} className={cn("p-4", a.is_enabled === false && "opacity-60")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <CardTitle>{a.name}</CardTitle>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {a.input_types.map((t) => (
                      <Badge key={t} tone="brand">{t}</Badge>
                    ))}
                    {a.is_enabled === false && <Badge tone="neutral">off</Badge>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="flex items-center gap-2">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={a.is_enabled !== false}
                      aria-label={`${a.is_enabled !== false ? "Disable" : "Enable"} ${a.name}`}
                      disabled={toggleEnabled.isPending}
                      onClick={() => toggleEnabled.mutate({ id: a.id, is_enabled: !(a.is_enabled !== false) })}
                      className={cn(
                        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition-colors",
                        "focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-2 dark:focus-visible:outline-[#2fdebf]",
                        a.is_enabled !== false
                          ? "border-transparent bg-[#2fdebf]"
                          : "border-[#e5e7eb] bg-[#e5e7eb] dark:border-white/10 dark:bg-white/10",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "inline-block h-5 w-5 rounded-full bg-white shadow transition-transform",
                          a.is_enabled !== false ? "translate-x-[22px]" : "translate-x-[2px]",
                        )}
                      />
                    </button>
                    <span className="font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                      {a.is_enabled !== false ? "On" : "Off"}
                    </span>
                  </span>
                  <Button variant="secondary" size="sm" onClick={() => setEditing({ ...a })} aria-label={`Edit ${a.name}`}>
                    <Pencil className="h-4 w-4" aria-hidden="true" />
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    onClick={() => window.confirm(`Delete agent "${a.name}"? Shared attributes stay with their other agents.`) && remove.mutate(a.id)}
                    aria-label={`Delete ${a.name}`}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </div>
              </div>
              <details className="mt-3">
                <summary className="cursor-pointer font-heading text-xs font-semibold text-[#4a5058] dark:text-[#C3C2B7]">
                  System instruction
                </summary>
                <pre className="mt-2 whitespace-pre-wrap rounded-xl bg-[#f1f2f3] p-3 font-sans text-xs text-[#1d1d1d] dark:bg-white/5 dark:text-[#F0EFEC]">
                  {a.system_instruction || "—"}
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
            <p className="mt-1 font-sans text-xs font-normal normal-case tracking-normal text-[#8a8f98]">
              New agents start from the shared default contract above — edit freely.
            </p>
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
