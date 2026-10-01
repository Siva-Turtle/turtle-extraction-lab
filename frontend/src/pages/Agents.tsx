import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Copy, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardTitle } from "../components/ui/Card";
import { Modal, fieldInput, fieldLabel, fieldTextarea } from "../components/ui/Modal";
import { PageHeader } from "../components/ui/PageHeader";
import type { LabAttribute } from "./Attributes";

export type Agent = {
  id: string;
  name: string;
  system_instruction: string;
  input_types: string[];
  is_enabled: boolean;
};

const INPUT_TYPES = ["transcription", "messages", "mail"];
export const DEFAULT_SYSTEM_INSTRUCTION = `Extract only information stated in the input transcription.\n\nReturn a JSON object keyed by attribute name. Each value has "value", "confidence" (0-1), "confidence_type" (quoted|inferred|normalized), "evidence" (exact quote). Omit attributes not found — never return null. quoted = stated word-for-word; inferred = concluded but not stated verbatim; normalized = standardized from a stated form (phone digits, dates, casing).\n\n| Type | Meaning |\n|---|---|\n| \`quoted\` | Value is explicitly stated in the transcript |\n| \`normalized\` | Value is explicitly stated but transformed into your canonical representation |\n| \`inferred\` | Value was not directly stated; model derived it from evidence |\n| \`not_found\` | No sufficient evidence exists |\n\nThe attribute list is attached automatically; the transcription arrives as the input message.`;

type Editing = {
  id?: string;
  name: string;
  system_instruction: string;
  input_types: string[];
  is_enabled: boolean;
};

type SaveInput = Editing & { attribute_ids: string[] };

const EMPTY: Editing = {
  name: "",
  system_instruction: DEFAULT_SYSTEM_INSTRUCTION,
  input_types: [],
  is_enabled: true,
};

/** Mirrors backend result contract (runs router) so the local build matches server truth. */
const RESULT_CONTRACT =
  'Return a JSON object keyed by attribute name. Each value is an object with "value" ' +
  '(the extracted value), "confidence" (0-1), "confidence_type" (quoted|inferred|normalized), ' +
  '"evidence" (exact quote from the input). If an attribute is not found in the input, ' +
  'omit it from the response — never return null. ' +
  'quoted = value stated word-for-word (evidence is the exact quote); ' +
  'inferred = value concluded from the input but not stated verbatim ' +
  '(evidence is the supporting passage); normalized = value standardized from a stated form ' +
  'such as phone digits, date formats, or casing (evidence is the original stated form).\n\n' +
  '| Type | Meaning |\n' +
  '|---|---|\n' +
  '| `quoted` | Value is explicitly stated in the transcript |\n' +
  '| `normalized` | Value is explicitly stated but transformed into your canonical representation |\n' +
  '| `inferred` | Value was not directly stated; model derived it from evidence |\n' +
  '| `not_found` | No sufficient evidence exists |';

const COPY_USER_TEMPLATE = "{{Transcription}}";

type PreviewObjectProp = {
  name: string;
  type: string;
  null_allowed: boolean;
};

type PreviewAttr = {
  name: string;
  type: string;
  description: string;
  group: string;
  enum_values: string[];
  object_properties: PreviewObjectProp[];
  array_items: { kind: string; properties: PreviewObjectProp[] };
};

/** Mirrors backend `_attr_line`: `- name (type): description [a | b]`. */
function attrLine(a: PreviewAttr): string {
  const base = `- ${a.name} (${a.type}): ${a.description}`;
  if (a.type === "enum" && a.enum_values.length > 0) {
    return `${base} [${a.enum_values.join(" | ")}]`;
  }
  return base;
}

/** Mirrors backend `_agent_system_content`. */
function buildLocalSystem(baseInstruction: string, attrs: PreviewAttr[]): string {
  const base = baseInstruction || "Extract structured data.";
  const lines = attrs.map(attrLine).join("\n") || "- (no attributes defined)";
  return `${base}\n\nAttributes to extract:\n${lines}\n\n${RESULT_CONTRACT}`;
}

/** Mirrors backend `_sub_schema`: one object sub-field to its schema fragment. */
function subSchema(subType: string, nullAllowed: boolean): Record<string, unknown> {
  const t = (base: string) => (nullAllowed ? [base, "null"] : base);
  if (subType === "array") {
    return { type: t("array"), items: { type: "string" } };
  }
  if (subType === "number") {
    return { type: t("number") };
  }
  if (subType === "boolean") {
    return { type: t("boolean") };
  }
  return { type: t("string") };
}

/** Mirrors backend `_array_item_schema`: array items fragment by kind. */
function arrayItemSchema(a: PreviewAttr): Record<string, unknown> {
  const kind = a.array_items?.kind ?? "string";
  if (kind === "number") {
    return { type: "number" };
  }
  if (kind === "object") {
    const subProps: Record<string, unknown> = {};
    const subRequired: string[] = [];
    for (const p of a.array_items?.properties ?? []) {
      subProps[p.name] = subSchema(p.type, p.null_allowed !== false);
      subRequired.push(p.name);
    }
    return { type: "object", properties: subProps, required: subRequired, additionalProperties: false };
  }
  return { type: "string" };
}

/** Mirrors backend `build_extraction_schema` + `build_chat_payload` envelope (strict meeting_extraction). */
function buildLocalResponseFormat(attrs: PreviewAttr[]): Record<string, unknown> {
  if (attrs.length === 0) {
    return { type: "json_object" };
  }
  const properties: Record<string, unknown> = {};
  for (const a of attrs) {
    let valueSchema: Record<string, unknown>;
    if (a.type === "number") {
      valueSchema = { type: ["number", "null"], description: `Extracted value for ${a.name}` };
    } else if (a.type === "boolean") {
      valueSchema = { type: ["boolean", "null"], description: `Extracted value for ${a.name}` };
    } else if (a.type === "array") {
      valueSchema = {
        type: ["array", "null"],
        items: arrayItemSchema(a),
        description: `Extracted value for ${a.name}`,
      };
    } else if (a.type === "object") {
      const subProps: Record<string, unknown> = {};
      const subRequired: string[] = [];
      for (const p of a.object_properties ?? []) {
        subProps[p.name] = subSchema(p.type, p.null_allowed !== false);
        subRequired.push(p.name);
      }
      valueSchema = {
        type: ["object", "null"],
        properties: subProps,
        required: subRequired,
        additionalProperties: false,
        description: `Extracted value for ${a.name}`,
      };
    } else {
      valueSchema = { type: ["string", "null"], description: `Extracted value for ${a.name}` };
    }
    if (a.type === "enum" && a.enum_values.length > 0) {
      valueSchema.enum = [...a.enum_values, null];
    }
    properties[a.name] = {
      type: "object",
      description: a.description || a.name,
      properties: {
        value: valueSchema,
        confidence: { type: "number", minimum: 0, maximum: 1 },
        confidence_type: {
          type: "string",
          enum: ["quoted", "inferred", "normalized", "not_found"],
        },
        evidence: { type: ["string", "null"] },
      },
      required: ["value", "confidence", "confidence_type", "evidence"],
      additionalProperties: false,
    };
  }
  return {
    type: "json_schema",
    json_schema: {
      name: "meeting_extraction",
      strict: true,
      schema: {
        type: "object",
        properties,
        required: attrs.map((a) => a.name),
        additionalProperties: false,
      },
    },
  };
}

function toPreviewAttr(a: LabAttribute): PreviewAttr {
  return {
    name: a.name,
    type: a.type,
    description: a.description,
    group: a.group ?? "",
    enum_values: a.enum_values ?? [],
    object_properties: (a.object_properties ?? []).map((p) => ({ ...p })),
    array_items: a.array_items?.kind === "object"
      ? { kind: "object", properties: (a.array_items.properties ?? []).map((p) => ({ ...p })) }
      : { kind: a.array_items?.kind ?? "string", properties: [] },
  };
}

const dropdownTrigger =
  "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-2 font-sans text-sm " +
  "hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1 " +
  "dark:border-white/10 dark:bg-[#2e2e2e] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]";

const dropdownList =
  "absolute inset-x-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-2xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] animate-[turtle-fade-in_120ms_ease-out] dark:border-white/10 dark:bg-[#1a1a1a]";

function InputTypesField({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (v: string[]) => void;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);

  function toggle(t: string) {
    onChange(selected.includes(t) ? selected.filter((x) => x !== t) : [...selected, t]);
  }

  const label =
    selected.length === 0
      ? "Select input types…"
      : selected.length === 1
        ? selected[0]
        : `${selected[0]} + ${selected.length - 1} other${selected.length - 1 === 1 ? "" : "s"}`;

  return (
    <div>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="Input types"
          className={dropdownTrigger}
        >
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-left",
              selected.length > 0 ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98] dark:text-[#898781]",
            )}
          >
            {label}
          </span>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10">
            <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
          </span>
        </button>
        {open && (
          <>
            <button
              type="button"
              aria-hidden="true"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className="fixed inset-0 z-10 cursor-default bg-transparent"
            />
            <div role="listbox" aria-label="Input types" className={cn(dropdownList, "z-20")}>
              <div className="grid gap-1.5">
                {INPUT_TYPES.map((t) => (
                  <label
                    key={t}
                    className={cn(
                      "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 font-sans text-sm",
                      selected.includes(t)
                        ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                        : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                    )}
                  >
                    <input type="checkbox" checked={selected.includes(t)} onChange={() => toggle(t)} />
                    <span className="min-w-0 flex-1 truncate">{t}</span>
                    {selected.includes(t) && (
                      <Check className="h-4 w-4 shrink-0 text-[#0d5c4a] dark:text-[#2fdebf]" aria-hidden="true" />
                    )}
                  </label>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function AttributeMultiSelect({
  all,
  selectedIds,
  onChange,
}: {
  all: LabAttribute[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const orderedNames = selectedIds
    .map((id) => all.find((a) => a.id === id)?.name)
    .filter((n): n is string => typeof n === "string" && n !== "");
  const q = filter.trim().toLowerCase();
  const visible = q
    ? all.filter(
        (a) =>
          a.name.toLowerCase().includes(q) ||
          (a.group ?? "").toLowerCase().includes(q),
      )
    : all;

  const grouped = useMemo(() => {
    const map = new Map<string, LabAttribute[]>();
    for (const a of visible) {
      const g = (a.group ?? "").trim();
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(a);
    }
    return [...map.entries()].sort((x, y) => x[0].localeCompare(y[0]));
  }, [visible]);

  function toggle(id: string) {
    onChange(selectedIds.includes(id) ? selectedIds.filter((x) => x !== id) : [...selectedIds, id]);
  }

  function toggleGroup(items: LabAttribute[]) {
    const ids = items.map((a) => a.id);
    const allSelected = ids.every((id) => selectedIds.includes(id));
    onChange(allSelected
      ? selectedIds.filter((id) => !ids.includes(id))
      : [...selectedIds, ...ids.filter((id) => !selectedIds.includes(id))]);
  }

  const label =
    orderedNames.length === 0
      ? "Select attributes…"
      : orderedNames.length === 1
        ? orderedNames[0]
        : `${orderedNames[0]} + ${orderedNames.length - 1} other${orderedNames.length - 1 === 1 ? "" : "s"}`;

  return (
    <div>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="Attributes"
          className={dropdownTrigger}
        >
          <span
            className={cn(
              "min-w-0 flex-1 truncate text-left",
              orderedNames.length > 0 ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98] dark:text-[#898781]",
            )}
          >
            {label}
          </span>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10">
            <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
          </span>
        </button>
        {open && (
          <>
            <button
              type="button"
              aria-hidden="true"
              tabIndex={-1}
              onClick={() => setOpen(false)}
              className="fixed inset-0 z-10 cursor-default bg-transparent"
            />
            <div
              role="listbox"
              aria-label="Attributes"
              className={cn(dropdownList, "z-20")}
            >
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter…"
                aria-label="Filter attributes"
                className={fieldInput}
              />
              <div className="mt-2 grid gap-1">
                {visible.length === 0 ? (
                  <p className="px-2 py-1 font-sans text-xs text-[#8a8f98]">
                    {all.length === 0 ? "No attributes yet — define them on the Attributes tab." : "No matches."}
                  </p>
                ) : q ? (
                  visible.map((a) => (
                    <label
                      key={a.id}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 font-sans text-sm",
                        selectedIds.includes(a.id)
                          ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                          : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                      )}
                    >
                      <input type="checkbox" checked={selectedIds.includes(a.id)} onChange={() => toggle(a.id)} />
                      <span className="min-w-0 flex-1 truncate">{a.name}</span>
                      <Badge tone="neutral">{a.type}</Badge>
                    </label>
                  ))
                ) : (
                  grouped.map(([g, items]) => {
                    const ids = items.map((a) => a.id);
                    const selectedCount = ids.filter((id) => selectedIds.includes(id)).length;
                    const allSelected = selectedCount === ids.length;
                    return (
                      <div key={g || "__ungrouped"}>
                        <label
                          className={cn(
                            "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 font-sans text-xs font-bold uppercase tracking-wide",
                            allSelected
                              ? "bg-[#e8fbf6] text-[#0d5c4a] dark:bg-white/10 dark:text-[#2fdebf]"
                              : "text-[#8a8f98]",
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={allSelected}
                            ref={(el) => {
                              if (el) el.indeterminate = selectedCount > 0 && !allSelected;
                            }}
                            onChange={() => toggleGroup(items)}
                            aria-label={g ? `Select all in ${g}` : "Select all ungrouped"}
                          />
                          <span className="min-w-0 flex-1 truncate">
                            {g || "Ungrouped"} ({selectedCount}/{ids.length})
                          </span>
                        </label>
                        {items.map((a) => (
                          <label
                            key={a.id}
                            className={cn(
                              "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 pl-8 font-sans text-sm",
                              selectedIds.includes(a.id)
                                ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                                : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                            )}
                          >
                            <input type="checkbox" checked={selectedIds.includes(a.id)} onChange={() => toggle(a.id)} />
                            <span className="min-w-0 flex-1 truncate">{a.name}</span>
                            <Badge tone="neutral">{a.type}</Badge>
                          </label>
                        ))}
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </>
        )}
      </div>
      {all.length === 0 && (
        <p className="mt-1 font-sans text-xs font-normal normal-case tracking-normal text-[#8a8f98]">
          No attributes yet — define them on the Attributes tab.
        </p>
      )}
    </div>
  );
}

export default function Agents() {
  const qc = useQueryClient();
  const { data = [], isLoading } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as Agent[],
  });
  const { data: allAttrs = [] } = useQuery({
    queryKey: ["attributes", ""],
    queryFn: async () => (await api.get("/attributes")).data as LabAttribute[],
  });
  const [editing, setEditing] = useState<Editing | null>(null);
  /** Attribute selection override; null = derive from the fetched attributes list. */
  const [selIds, setSelIds] = useState<string[] | null>(null);

  function closeEditor() {
    setEditing(null);
    setSelIds(null);
  }

  function openNew() {
    setEditing({ ...EMPTY, input_types: [] });
    setSelIds([]);
  }

  function openEdit(a: Agent) {
    setEditing({ id: a.id, name: a.name, system_instruction: a.system_instruction, input_types: [...a.input_types], is_enabled: a.is_enabled });
    setSelIds(null);
  }

  const eid = editing?.id;
  const effectiveIds: string[] =
    selIds ?? (eid ? allAttrs.filter((a) => (a.agent_ids ?? []).includes(eid)).map((a) => a.id) : []);
  const selectedAttrs = allAttrs.filter((a) => effectiveIds.includes(a.id));

  const save = useMutation({
    mutationFn: async (v: SaveInput) => {
      if (v.id) {
        const body = {
          name: v.name,
          system_instruction: v.system_instruction,
          input_types: v.input_types,
          is_enabled: v.is_enabled,
          attribute_ids: v.attribute_ids,
        };
        return (await api.patch(`/agents/${v.id}`, body)).data;
      }
      const created = (
        await api.post("/agents", {
          name: v.name,
          system_instruction: v.system_instruction,
          input_types: v.input_types,
          is_enabled: v.is_enabled,
        })
      ).data as Agent;
      if (v.attribute_ids.length > 0) {
        await api.patch(`/agents/${created.id}`, { attribute_ids: v.attribute_ids });
      }
      return created;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["attributes"] });
      closeEditor();
      toast.success("Agent saved");
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Save failed"),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => (await api.delete(`/agents/${id}`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["attributes"] });
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

  async function copyLlmRequest() {
    if (!editing) return;
    const attrs = selectedAttrs.map(toPreviewAttr);
    const system = buildLocalSystem(editing.system_instruction, attrs);
    const response_format = buildLocalResponseFormat(attrs);
    const payload = {
      model: "{{Model}}",
      messages: [
        { role: "system", content: system },
        { role: "user", content: COPY_USER_TEMPLATE },
      ],
      response_format,
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      toast.success("LLM request copied");
    } catch {
      toast.error("Copy failed");
    }
  }

  return (
    <div className="grid gap-4">
      <PageHeader
        title="Agents"
        actions={
          <Button size="sm" onClick={openNew}>
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
            <Card
              key={a.id}
              padded={false}
              onClick={() => openEdit(a)}
              className={cn("cursor-pointer p-4", a.is_enabled === false && "opacity-60")}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <CardTitle>{a.name}</CardTitle>
                    {a.input_types.map((t) => (
                      <Badge key={t} tone="brand">{t}</Badge>
                    ))}
                    {a.is_enabled === false && <Badge tone="neutral">off</Badge>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2" onClick={(e) => e.stopPropagation()}>
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
            </Card>
          ))}
        </div>
      )}

      {editing && (
        <Modal title={editing.id ? "Edit agent" : "New agent"} onClose={closeEditor} wide>
          <div className="grid gap-3">
            <label className={fieldLabel}>
              Name <span aria-hidden="true" className="text-[#ef4444]"> *</span>
              <input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className={fieldInput}
              />
            </label>
            <div className={fieldLabel}>
              Input types
              <div className="mt-1.5">
                <InputTypesField
                  selected={editing.input_types}
                  onChange={(v) => setEditing({ ...editing, input_types: v })}
                />
              </div>
            </div>
            <div className={fieldLabel}>
              Attributes
              <div className="mt-1.5">
                <AttributeMultiSelect all={allAttrs} selectedIds={effectiveIds} onChange={setSelIds} />
              </div>
            </div>
            <label className={cn(fieldLabel, "block")}>
              System instruction
              <textarea
                value={editing.system_instruction}
                onChange={(e) => setEditing({ ...editing, system_instruction: e.target.value })}
                rows={6}
                className={fieldTextarea}
              />
            </label>
          </div>
          <div className="mt-5 flex items-center justify-between gap-2">
            <Button variant="secondary" size="sm" onClick={copyLlmRequest} aria-label="Copy LLM request">
              <Copy className="h-4 w-4" aria-hidden="true" />
            </Button>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={closeEditor}>
                Cancel
              </Button>
              <Button
                loading={save.isPending}
                onClick={() =>
                  editing.name.trim()
                    ? save.mutate({ ...editing, attribute_ids: effectiveIds })
                    : toast.error("Name is required")
                }
              >
                Save
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
