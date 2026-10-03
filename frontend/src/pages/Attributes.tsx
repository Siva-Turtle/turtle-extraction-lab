import * as React from "react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import type { AttributeArrayItems, AttributeArrayKind } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Modal, fieldInput, fieldLabel, fieldTextarea } from "../components/ui/Modal";
import { MultiSelectFilter } from "../components/ui/Combobox";
import { PageHeader } from "../components/ui/PageHeader";
import type { Agent } from "./Agents";

export type AttributeType = "string" | "number" | "boolean" | "enum" | "array" | "object";

/** Sub-field of a type=="object" attribute (dict in code comments, "object" in the UI). */
export type AttributeObjectProperty = {
  name: string;
  type: "string" | "number" | "integer" | "boolean" | "array";
  null_allowed: boolean;
  enum?: string[];
  description?: string;
};

export type LabAttribute = {
  id: string;
  agent_ids: string[];
  name: string;
  type: AttributeType;
  description: string;
  group: string;
  enum_values: string[];
  object_properties: AttributeObjectProperty[];
  array_items: AttributeArrayItems;
  wrap_result?: boolean;
};

const TYPES: AttributeType[] = ["string", "number", "boolean", "enum", "array", "object"];
const SUB_TYPES: AttributeObjectProperty["type"][] = ["string", "number", "integer", "boolean", "array"];
const ARRAY_KINDS: AttributeArrayKind[] = ["string", "number", "object"];
const EMPTY_ARRAY_ITEMS: AttributeArrayItems = { kind: "string", properties: [] };
const EMPTY: {
  name: string;
  type: AttributeType;
  description: string;
  group: string;
  enum_values: string[];
  object_properties: AttributeObjectProperty[];
  array_items: AttributeArrayItems;
  wrap_result: boolean;
} = { name: "", type: "string", description: "", group: "", enum_values: [], object_properties: [], array_items: { ...EMPTY_ARRAY_ITEMS, properties: [] }, wrap_result: true };

/** v2 single-select for attribute type — mirrors FilterCombobox styling. */
function TypeCombobox({
  value,
  onChange,
}: {
  value: AttributeType;
  onChange: (v: AttributeType) => void;
}): React.JSX.Element {
  const [open, setOpen] = React.useState(false);
  const [highlight, setHighlight] = React.useState(0);
  const rootRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function pick(v: AttributeType) {
    onChange(v);
    setOpen(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, TYPES.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(TYPES[highlight]);
    }
  }

  return (
    <div ref={rootRef} className="relative mt-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKey}
        role="combobox"
        aria-expanded={open}
        aria-label="Attribute type"
        className={cn(
          "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-2 font-sans text-sm",
          "hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1",
          "dark:border-white/10 dark:bg-[#2e2e2e] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]",
        )}
      >
        <span className="min-w-0 flex-1 truncate text-left text-[#1d1d1d] dark:text-[#F0EFEC]">
          {value}
        </span>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10">
          <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
        </span>
      </button>
      {open && (
        <ul
          role="listbox"
          aria-label="Attribute type"
          className="absolute inset-x-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-2xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] animate-[turtle-fade-in_120ms_ease-out] dark:border-white/10 dark:bg-[#1a1a1a]"
        >
          {TYPES.map((t, i) => (
            <li key={t} role="option" aria-selected={value === t}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(t)}
                onMouseEnter={() => setHighlight(i)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left font-sans text-sm",
                  i === highlight
                    ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                    : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                )}
              >
                <span className="min-w-0 flex-1 truncate">{t}</span>
                {value === t && (
                  <Check className="h-4 w-4 shrink-0 text-[#0d5c4a] dark:text-[#2fdebf]" aria-hidden="true" />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Per-value inputs with plus-to-add; dedupe exact (case-sensitive) with inline error. */
function EnumValuesEditor({
  values,
  onChange,
}: {
  values: string[];
  onChange: (v: string[]) => void;
}): React.JSX.Element {
  const display = values.length > 0 ? values : [""];
  const trimmed = display.map((v) => v.trim()).filter((v) => v !== "");
  const counts = new Map<string, number>();
  for (const v of trimmed) counts.set(v, (counts.get(v) ?? 0) + 1);
  const dupes = new Set([...counts.entries()].filter(([, n]) => n > 1).map(([v]) => v));
  const isDupeRow = (raw: string) => raw.trim() !== "" && dupes.has(raw.trim());

  function setRow(i: number, next: string) {
    if (values.length === 0) {
      onChange([next]);
      return;
    }
    onChange(values.map((v, idx) => (idx === i ? next : v)));
  }

  function removeRow(i: number) {
    if (values.length === 0) {
      onChange([]);
      return;
    }
    onChange(values.filter((_, idx) => idx !== i));
  }

  function addRow() {
    const last = display[display.length - 1] ?? "";
    if (last.trim() === "") return;
    onChange([...display]);
  }

  const lastEmpty = (display[display.length - 1] ?? "").trim() === "";

  return (
    <div>
      <div className="grid gap-2">
        {display.map((v, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={v}
              onChange={(e) => setRow(i, e.target.value)}
              spellCheck={false}
              placeholder={`Value ${i + 1}`}
              aria-label={`Allowed value ${i + 1}`}
              className={cn(fieldInput, "mt-0 font-mono text-xs", isDupeRow(v) && "border-[#ef4444]")}
            />
            <button
              type="button"
              onClick={() => removeRow(i)}
              aria-label={`Remove value ${i + 1}`}
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] hover:text-[#b91c1c] dark:text-[#C3C2B7] dark:hover:bg-white/10"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
      {dupes.size > 0 && (
        <p role="alert" className="mt-1 font-sans text-xs text-[#b91c1c] dark:text-[#f87171]">
          Each value must be unique — duplicate: {[...dupes].join(", ")}
        </p>
      )}
      <div className="mt-2">
        <Button variant="secondary" size="sm" onClick={addRow} disabled={lastEmpty} aria-label="Add value">
          <Plus className="h-4 w-4" aria-hidden="true" /> Add value
        </Button>
      </div>
    </div>
  );
}

/** Ordered sub-fields for type=="object" (dict): name + limited type + null flag.
 * Plus-to-add; no new row while the last name is empty; exact-match dedupe.
 * Each row also carries optional `enum` (pipe-separated allowed values) and
 * `description` help-text (emitted as JSON-schema description). */
function ObjectPropertiesEditor({
  properties,
  onChange,
}: {
  properties: AttributeObjectProperty[];
  onChange: (v: AttributeObjectProperty[]) => void;
}): React.JSX.Element {
  const display: AttributeObjectProperty[] =
    properties.length > 0 ? properties : [{ name: "", type: "string", null_allowed: true, enum: [], description: "" }];
  const trimmed = display.map((p) => p.name.trim()).filter((n) => n !== "");
  const counts = new Map<string, number>();
  for (const n of trimmed) counts.set(n, (counts.get(n) ?? 0) + 1);
  const dupes = new Set([...counts.entries()].filter(([, n]) => n > 1).map(([v]) => v));
  const isDupeRow = (raw: string) => raw.trim() !== "" && dupes.has(raw.trim());

  function setRow(i: number, next: AttributeObjectProperty) {
    if (properties.length === 0) {
      onChange([next]);
      return;
    }
    onChange(properties.map((p, idx) => (idx === i ? next : p)));
  }

  function removeRow(i: number) {
    if (properties.length === 0) {
      onChange([]);
      return;
    }
    onChange(properties.filter((_, idx) => idx !== i));
  }

  function addRow() {
    const last = display[display.length - 1] ?? { name: "" };
    if (last.name.trim() === "") return;
    onChange([...display, { name: "", type: "string", null_allowed: true, enum: [], description: "" }]);
  }

  const lastEmpty = ((display[display.length - 1] ?? { name: "" }).name ?? "").trim() === "";

  return (
    <div>
      <div className="grid gap-2">
        <div
          aria-hidden="true"
          className="grid grid-cols-[minmax(0,1fr)_128px_96px_36px] items-center gap-2 px-0 font-heading text-[11px] font-bold uppercase tracking-wide text-[#8a8f98]"
        >
          <span>Property name</span>
          <span>Type</span>
          <span className="text-center">Null allowed?</span>
          <span />
        </div>
        {display.map((p, i) => (
          <div key={i} className="grid gap-1.5">
            <div className="grid grid-cols-[minmax(0,1fr)_128px_96px_36px] items-center gap-2">
              <input
                value={p.name}
                onChange={(e) => setRow(i, { ...p, name: e.target.value })}
                spellCheck={false}
                placeholder={`Property ${i + 1}`}
                aria-label={`Property name ${i + 1}`}
                className={cn(fieldInput, "mt-0 font-mono text-xs", isDupeRow(p.name) && "border-[#ef4444]")}
              />
              <select
                value={p.type}
                onChange={(e) =>
                  setRow(i, { ...p, type: e.target.value as AttributeObjectProperty["type"] })
                }
                aria-label={`Property type ${i + 1}`}
                className={cn(fieldInput, "mt-0 font-mono text-xs")}
              >
                {SUB_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <input
                type="checkbox"
                checked={p.null_allowed}
                onChange={(e) => setRow(i, { ...p, null_allowed: e.target.checked })}
                aria-label={`Null allowed for property ${i + 1}`}
                className="mx-auto h-5 w-5 shrink-0 accent-[#0d5c4a] dark:accent-[#2fdebf]"
              />
              <button
                type="button"
                onClick={() => removeRow(i)}
                aria-label={`Remove property ${i + 1}`}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] hover:text-[#b91c1c] dark:text-[#C3C2B7] dark:hover:bg-white/10"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
            <div className="grid gap-1.5 sm:grid-cols-2">
              <input
                value={(p.enum ?? []).join(" | ")}
                onChange={(e) =>
                  setRow(i, {
                    ...p,
                    enum: e.target.value.split("|").map((s) => s.trim()).filter((s) => s !== ""),
                  })
                }
                spellCheck={false}
                placeholder="Allowed values, separated by | (optional)"
                aria-label={`Allowed values for property ${i + 1}`}
                className={cn(fieldInput, "mt-0 font-mono text-xs")}
              />
              <input
                value={p.description ?? ""}
                onChange={(e) => setRow(i, { ...p, description: e.target.value })}
                spellCheck={false}
                placeholder="Field help text (optional)"
                aria-label={`Help text for property ${i + 1}`}
                className={cn(fieldInput, "mt-0 font-mono text-xs")}
              />
            </div>
          </div>
        ))}
      </div>
      {dupes.size > 0 && (
        <p role="alert" className="mt-1 font-sans text-xs text-[#b91c1c] dark:text-[#f87171]">
          Each property name must be unique — duplicate: {[...dupes].join(", ")}
        </p>
      )}
      <div className="mt-2">
        <Button variant="secondary" size="sm" onClick={addRow} disabled={lastEmpty} aria-label="Add property">
          <Plus className="h-4 w-4" aria-hidden="true" /> Add property
        </Button>
      </div>
    </div>
  );
}

/** Item-shape config for type=="array": kind + object sub-fields when object. */
function ArrayItemsEditor({
  value,
  onChange,
}: {
  value: AttributeArrayItems;
  onChange: (v: AttributeArrayItems) => void;
}): React.JSX.Element {
  const kind = value?.kind ?? "string";
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Array item kind">
        {ARRAY_KINDS.map((k) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={kind === k}
            onClick={() => onChange(k === "object"
              ? { kind: k, properties: value?.properties ?? [] }
              : { kind: k, properties: [] })}
            className={cn(
              "rounded-full border px-3 py-1.5 font-sans text-xs",
              kind === k
                ? "border-transparent bg-[#0d5c4a] text-white dark:bg-[#2fdebf] dark:text-[#1a1a1a]"
                : "border-[#e5e7eb] text-[#4a5058] hover:border-[#1d1d1d] dark:border-white/10 dark:text-[#C3C2B7]",
            )}
          >
            {k === "object" ? "object[]" : `${k}[]`}
          </button>
        ))}
      </div>
      {kind === "object" && (
        <ObjectPropertiesEditor
          properties={value?.properties ?? []}
          onChange={(props) => onChange({ kind: "object", properties: props })}
        />
      )}
    </div>
  );
}

export default function Attributes() {
  const qc = useQueryClient();
  const { data: agents = [] } = useQuery({
    queryKey: ["agents"],
    queryFn: async () => (await api.get("/agents")).data as Agent[],
  });
  const { data = [], isLoading } = useQuery({
    queryKey: ["attributes"],
    queryFn: async () => (await api.get("/attributes")).data as LabAttribute[],
  });
  const [editing, setEditing] = useState<(typeof EMPTY & { id?: string }) | null>(null);
  const [groupFilter, setGroupFilter] = useState<string[]>([]);
  const [search, setSearch] = useState("");

  const groupOptions = React.useMemo(() => {
    const seen = new Map<string, number>();
    for (const r of data) {
      const g = (r.group ?? "").trim();
      if (g) seen.set(g, (seen.get(g) ?? 0) + 1);
    }
    return [...seen.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([g, n]) => ({ value: g, label: g, sub: `${n} attribute${n === 1 ? "" : "s"}` }));
  }, [data]);

  const visible = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    return data.filter((r) => {
      if (groupFilter.length > 0 && !groupFilter.includes((r.group ?? "").trim())) {
        return false;
      }
      if (!q) return true;
      return (
        (r.name ?? "").toLowerCase().includes(q) ||
        (r.description ?? "").toLowerCase().includes(q) ||
        (r.group ?? "").toLowerCase().includes(q)
      );
    });
  }, [data, groupFilter, search]);

  const save = useMutation({
    mutationFn: async (v: typeof EMPTY & { id?: string }) => {
      const raw = v.enum_values ?? [];
      const cleaned = raw.map((s) => s.trim()).filter((s) => s !== "");
      const cleanProp = (p: AttributeObjectProperty): AttributeObjectProperty => ({
        ...p,
        name: p.name.trim(),
        enum: (p.enum ?? []).map((s) => s.trim()).filter((s) => s !== ""),
        description: (p.description ?? "").trim(),
      });
      const rawProps = v.object_properties ?? [];
      const cleanedProps = rawProps
        .map(cleanProp)
        .filter((p) => p.name !== "");
      const rawItems = v.array_items ?? EMPTY_ARRAY_ITEMS;
      const cleanedItems: AttributeArrayItems =
        v.type === "array" && rawItems.kind === "object"
          ? { kind: "object", properties: (rawItems.properties ?? []).map(cleanProp).filter((p) => p.name !== "") }
          : v.type === "array" && rawItems.kind === "number"
            ? { kind: "number", properties: [] }
            : { kind: "string", properties: [] };
      const body = {
        name: v.name.trim(),
        type: v.type,
        description: v.description,
        group: (v.group ?? "").trim(),
        enum_values: v.type === "enum" ? cleaned : [],
        object_properties: v.type === "object" ? cleanedProps : [],
        array_items: v.type === "array" ? cleanedItems : { kind: "string", properties: [] },
        wrap_result: (v as { wrap_result?: boolean }).wrap_result !== false,
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
    setEditing({ ...EMPTY, group: "", enum_values: [], object_properties: [], array_items: { ...EMPTY_ARRAY_ITEMS, properties: [] }, wrap_result: true });
  }

  function agentNames(r: LabAttribute): string {
    const names = (r.agent_ids ?? []).map(
      (id) => agents.find((a) => a.id === id)?.name ?? id.slice(0, 8),
    );
    return names.length > 0 ? names.join(", ") : "—";
  }

  function trySave() {
    if (!editing) return;
    if (!editing.name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (editing.type === "enum") {
      const cleaned = (editing.enum_values ?? []).map((s) => s.trim()).filter((s) => s !== "");
      if (cleaned.length === 0) {
        toast.error("Enum needs at least one value");
        return;
      }
      if (new Set(cleaned).size !== cleaned.length) {
        toast.error("Each enum value must be unique");
        return;
      }
    }
    if (editing.type === "object") {
      const cleaned = (editing.object_properties ?? [])
        .map((p) => p.name.trim())
        .filter((n) => n !== "");
      if (cleaned.length === 0) {
        toast.error("Object needs at least one property");
        return;
      }
      if (new Set(cleaned).size !== cleaned.length) {
        toast.error("Each property name must be unique");
        return;
      }
    }
    if (editing.type === "array" && (editing.array_items?.kind ?? "string") === "object") {
      const cleaned = ((editing.array_items?.properties ?? []) as AttributeObjectProperty[])
        .map((p) => p.name.trim())
        .filter((n) => n !== "");
      if (cleaned.length === 0) {
        toast.error("Object arrays need at least one property");
        return;
      }
      if (new Set(cleaned).size !== cleaned.length) {
        toast.error("Each property name must be unique");
        return;
      }
    }
    save.mutate(editing);
  }

  function openEdit(r: LabAttribute) {
    const normProp = (p: AttributeObjectProperty): AttributeObjectProperty => ({
      ...p,
      enum: [...(p.enum ?? [])],
      description: p.description ?? "",
    });
    setEditing({
      ...r,
      group: r.group ?? "",
      enum_values: [...(r.enum_values ?? [])],
      object_properties: (r.object_properties ?? []).map(normProp),
      array_items: r.array_items?.kind === "object"
        ? { kind: "object", properties: (r.array_items.properties ?? []).map(normProp) }
        : r.array_items?.kind === "number"
          ? { kind: "number", properties: [] }
          : { kind: "string", properties: [] },
      wrap_result: (r as { wrap_result?: boolean }).wrap_result !== false,
    });
  }

  return (
    <div className="grid gap-4">
      <PageHeader
        title="Attributes"
        actions={
          <div className="flex items-center gap-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search attributes…"
              aria-label="Search attributes"
              spellCheck={false}
              className="mt-1.5 h-11 w-56 rounded-xl border border-[#e5e7eb] bg-white px-3 font-sans text-sm text-[#1d1d1d] placeholder:text-[#8a8f98] hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1 dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC] dark:placeholder:text-[#898781] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]"
            />
            {groupOptions.length > 0 && (
              <div className="w-56">
                <MultiSelectFilter
                  options={groupOptions}
                  selected={groupFilter}
                  onChange={setGroupFilter}
                  placeholder="Filter by group…"
                  ariaLabel="Filter by group"
                />
              </div>
            )}
            <Button size="md" onClick={startNew} className="mt-1.5">
              <Plus className="h-4 w-4" aria-hidden="true" /> New attribute
            </Button>
          </div>
        }
      />

      {isLoading ? (
        <Card>
          <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
        </Card>
      ) : data.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            No attributes here yet — create the first one, then map it on the agent card.
          </p>
        </Card>
      ) : visible.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            No attributes match — clear the search or group filter to see all.
          </p>
        </Card>
      ) : (
        <Card padded={false} className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-[#e5e7eb] font-heading text-xs font-bold uppercase tracking-wide text-[#8a8f98] dark:border-white/10">
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Group</th>
                <th className="px-4 py-3">Agents</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Values</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => (
                <tr
                  key={r.id}
                  onClick={() => openEdit(r)}
                  className="cursor-pointer border-b border-[#e5e7eb] last:border-0 hover:bg-[#e8fbf6]/50 dark:border-white/10 dark:hover:bg-white/5"
                >
                  <td className="px-4 py-3 font-heading font-semibold text-[#1d1d1d] dark:text-[#F0EFEC]">{r.name}</td>
                  <td className="px-4 py-3 text-[#4a5058] dark:text-[#C3C2B7]">
                    {(r.group ?? "").trim() || "—"}
                  </td>
                  <td className="px-4 py-3 text-[#4a5058] dark:text-[#C3C2B7]">
                    {agentNames(r)}
                  </td>
                  <td className="px-4 py-3"><Badge tone="neutral">{r.type === "array" ? `array[${r.array_items?.kind ?? "string"}]` : r.type}</Badge></td>
                  <td className="px-4 py-3">
                    {r.type === "enum" && (r.enum_values ?? []).length > 0 ? (
                      <span className="flex flex-wrap gap-1">
                        {(r.enum_values ?? []).map((v) => (
                          <Badge key={v} tone="neutral">{v}</Badge>
                        ))}
                      </span>
                    ) : r.type === "object" && (r.object_properties ?? []).length > 0 ? (
                      <span className="flex flex-wrap gap-1">
                        {(r.object_properties ?? []).map((p) => (
                          <Badge key={p.name} tone="neutral">{p.name} ({p.type})</Badge>
                        ))}
                      </span>
                    ) : r.type === "array" && (r.array_items?.kind ?? "string") === "object" && (r.array_items?.properties ?? []).length > 0 ? (
                      <span className="flex flex-wrap gap-1">
                        {(r.array_items?.properties ?? []).map((p) => (
                          <Badge key={p.name} tone="neutral">{p.name} ({p.type})</Badge>
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
                        onClick={(e) => {
                          e.stopPropagation();
                          if (window.confirm(`Delete attribute "${r.name}"?`)) remove.mutate(r.id);
                        }}
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
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className={fieldLabel}>
              Name <span aria-hidden="true" className="text-[#ef4444]"> *</span>
              <input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className={fieldInput}
              />
            </label>
            <div className={fieldLabel}>
              Type
              <TypeCombobox
                value={editing.type}
                onChange={(next) =>
                  setEditing({
                    ...editing,
                    type: next,
                    enum_values: next === "enum" ? editing.enum_values : [],
                    object_properties: next === "object" ? editing.object_properties : [],
                    array_items: next === "array" ? editing.array_items : { ...EMPTY_ARRAY_ITEMS, properties: [] },
                  })
                }
              />
            </div>
          </div>
          <label className={cn(fieldLabel, "mt-3 block")}>
            Group
            <input
              value={editing.group ?? ""}
              onChange={(e) => setEditing({ ...editing, group: e.target.value })}
              spellCheck={false}
              placeholder="e.g. Assets"
              aria-label="Attribute group"
              list="attribute-group-options"
              className={fieldInput}
            />
            <datalist id="attribute-group-options">
              {groupOptions.map((o) => (
                <option key={o.value} value={o.value} />
              ))}
            </datalist>
          </label>
          <label className={cn(fieldLabel, "mt-3 block")}>
            Description
            <textarea
              value={editing.description}
              onChange={(e) => setEditing({ ...editing, description: e.target.value })}
              rows={4}
              className={fieldTextarea}
            />
          </label>
          {editing.type === "enum" && (
            <div className={cn(fieldLabel, "mt-3 block")}>
              Allowed values <span aria-hidden="true" className="text-[#ef4444]"> *</span>
              <div className="mt-1.5">
                <EnumValuesEditor
                  values={editing.enum_values}
                  onChange={(v) => setEditing({ ...editing, enum_values: v })}
                />
              </div>
            </div>
          )}
          {editing.type === "object" && (
            <div className={cn(fieldLabel, "mt-3 block")}>
              Properties <span aria-hidden="true" className="text-[#ef4444]"> *</span>
              <div className="mt-1.5">
                <ObjectPropertiesEditor
                  properties={editing.object_properties}
                  onChange={(v) => setEditing({ ...editing, object_properties: v })}
                />
              </div>
            </div>
          )}
          {editing.type === "array" && (
            <div className={cn(fieldLabel, "mt-3 block")}>
              Item shape
              <div className="mt-1.5">
                <ArrayItemsEditor
                  value={editing.array_items}
                  onChange={(v) => setEditing({ ...editing, array_items: v })}
                />
              </div>
            </div>
          )}
          <label className="mt-3 flex cursor-pointer items-center gap-2 font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]">
            <input
              type="checkbox"
              checked={(editing as { wrap_result?: boolean }).wrap_result !== false}
              onChange={(e) => setEditing({ ...editing, wrap_result: e.target.checked })}
              aria-label="Wrap result (value / confidence / evidence)"
              className="h-5 w-5 shrink-0 accent-[#0d5c4a] dark:accent-[#2fdebf]"
            />
            <span>
              Wrap result (value / confidence / evidence)
              <span className="block text-xs font-normal text-[#8a8f98]">
                Off returns the list directly — confidence lives per item (v2 list attributes).
              </span>
            </span>
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <Button loading={save.isPending} onClick={trySave}>
              Save
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
