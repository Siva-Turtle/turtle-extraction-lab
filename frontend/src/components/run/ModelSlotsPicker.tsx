import * as React from "react";
import { toast } from "sonner";
import type { ModelInfo } from "../../lib/api";
import { shortModel } from "../../lib/format";
import type { ModelSlot } from "../../lib/logTypes";
import { effortOptionsFor } from "../../lib/models";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ModelCombobox, loadFavouriteModels } from "../ui/Combobox";

const PRESETS_KEY = "lab:model-presets";

type Preset = { name: string; slots: ModelSlot[] };

function isModelSlot(v: unknown): v is ModelSlot {
  if (!v || typeof v !== "object") return false;
  const s = v as Record<string, unknown>;
  return typeof s.model === "string" && typeof s.effort === "string";
}

function loadPresets(): Preset[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const out: Preset[] = [];
    for (const p of parsed) {
      if (!p || typeof p !== "object") continue;
      const rec = p as Record<string, unknown>;
      if (typeof rec.name !== "string" || !Array.isArray(rec.slots)) continue;
      const slots = (rec.slots as unknown[]).filter(isModelSlot).map((s) => ({
        model: s.model,
        effort: s.effort,
      }));
      out.push({ name: rec.name, slots });
    }
    return out;
  } catch {
    return [];
  }
}

function savePresets(presets: Preset[]) {
  try {
    localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
  } catch {
    // ignore persistence failures (private mode, quota)
  }
}

/**
 * Multi-model slot picker (replaces the single Model + Reasoning effort
 * fields). One chip per `{model, effort}` slot in picker order; an "add"
 * combobox, a favourites row, and localStorage presets below.
 */
export function ModelSlotsPicker({
  slots,
  onChange,
  models,
  max = 4,
}: {
  slots: ModelSlot[];
  onChange: (s: ModelSlot[]) => void;
  models: ModelInfo[];
  max?: number;
}): React.JSX.Element {
  // effortOptionsFor wants the /models meta shape; the live flag only gates
  // the "no reasoning options" disabled state, and a populated list means
  // the backend answered, so treat it as live.
  const meta = React.useMemo(() => ({ live: true, models }), [models]);
  const [presets, setPresets] = React.useState<Preset[]>(loadPresets);
  const [presetsOpen, setPresetsOpen] = React.useState(false);
  const [favTick, setFavTick] = React.useState(0);

  // Favourites are starred inside the combobox dropdown (which owns its own
  // state), so refresh the row when the window regains focus.
  React.useEffect(() => {
    const refresh = () => setFavTick((t) => t + 1);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);

  const favourites = React.useMemo((): string[] => {
    void favTick;
    return loadFavouriteModels().filter((f) => !slots.some((s) => s.model === f));
  }, [favTick, slots]);

  const full = slots.length >= max;

  function addModel(id: string) {
    const clean = id.trim();
    if (clean === "") return;
    if (slots.length >= max) {
      toast.error(`Max ${max} models`);
      return;
    }
    if (slots.some((s) => s.model === clean && s.effort === "")) {
      toast.error("Already added");
      return;
    }
    onChange([...slots, { model: clean, effort: "" }]);
  }

  function setEffort(index: number, effort: string) {
    const current = slots[index];
    if (!current) return;
    if (slots.some((s, i) => i !== index && s.model === current.model && s.effort === effort)) {
      toast.error("Already added");
      return;
    }
    onChange(slots.map((s, i) => (i === index ? { ...s, effort } : s)));
  }

  function removeSlot(index: number) {
    onChange(slots.filter((_, i) => i !== index));
  }

  function saveCurrentAsPreset() {
    const name = window.prompt("Preset name");
    const clean = (name ?? "").trim();
    if (clean === "") return;
    const next = [...presets.filter((p) => p.name !== clean), { name: clean, slots }];
    setPresets(next);
    savePresets(next);
  }

  function deletePreset(name: string) {
    const next = presets.filter((p) => p.name !== name);
    setPresets(next);
    savePresets(next);
  }

  return (
    <div className="grid gap-2">
      {slots.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Selected models">
          {slots.map((s, i) => {
            const effortInfo = effortOptionsFor(meta, s.model);
            return (
              <li
                key={`${s.model}|${s.effort}|${i}`}
                className="flex min-w-0 items-center gap-1.5 rounded-full border border-[#e5e7eb] bg-white py-1 pl-3 pr-1.5 dark:border-white/10 dark:bg-[#2e2e2e]"
              >
                <span
                  title={s.model}
                  className="max-w-40 truncate font-mono text-xs text-[#1d1d1d] dark:text-[#F0EFEC]"
                >
                  {shortModel(s.model)}
                </span>
                <select
                  value={s.effort}
                  onChange={(e) => setEffort(i, e.target.value)}
                  disabled={effortInfo.disabled}
                  aria-label={`Reasoning effort for ${s.model}`}
                  title={effortInfo.disabled ? "No reasoning options for this model" : "Reasoning effort"}
                  className="h-7 max-w-28 rounded-full border border-[#e5e7eb] bg-white px-1.5 font-sans text-xs text-[#1d1d1d] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:bg-[#1a1a1a] dark:text-[#F0EFEC]"
                >
                  <option value="">default</option>
                  {effortInfo.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={() => removeSlot(i)}
                  aria-label={`Remove ${s.model}`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#fdecec] hover:text-[#b91c1c] dark:text-[#C3C2B7] dark:hover:bg-[#ef4444]/15 dark:hover:text-[#f87171]"
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div title={full ? `Max ${max} models` : undefined}>
        <ModelCombobox mode="add" onChange={addModel} disabled={full} />
      </div>
      {favourites.length > 0 && !full && (
        <div className="flex flex-wrap items-center gap-1.5" aria-label="Favourite models">
          <span className="font-heading text-[11px] font-bold uppercase tracking-wide text-[#8a8f98]">
            Favourites
          </span>
          {favourites.map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => addModel(f)}
              title={f}
              className="max-w-48 truncate rounded-full border border-[#e5e7eb] bg-[#f1f2f3] px-2.5 py-1 font-mono text-[11px] text-[#4a5058] hover:border-[#1d1d1d] hover:text-[#1d1d1d] dark:border-white/10 dark:bg-white/10 dark:text-[#C3C2B7] dark:hover:border-white/40 dark:hover:text-[#F0EFEC]"
            >
              {shortModel(f)}
            </button>
          ))}
        </div>
      )}
      <div>
        <Button variant="secondary" size="sm" onClick={() => setPresetsOpen((o) => !o)} aria-expanded={presetsOpen}>
          Presets
          {presets.length > 0 && (
            <Badge tone="neutral" className="ml-1">
              {presets.length}
            </Badge>
          )}
        </Button>
        {presetsOpen && (
          <div className="mt-1.5 grid gap-1 rounded-xl border border-[#e5e7eb] bg-white p-1.5 dark:border-white/10 dark:bg-[#1a1a1a]">
            <button
              type="button"
              onClick={saveCurrentAsPreset}
              className="rounded-lg px-3 py-2 text-left font-sans text-sm text-[#1d1d1d] hover:bg-[#e8fbf6] dark:text-[#F0EFEC] dark:hover:bg-white/10"
            >
              Save current as preset…
            </button>
            {presets.map((p) => (
              <div
                key={p.name}
                className="flex items-center gap-1 rounded-lg px-1 py-0.5 hover:bg-[#e8fbf6] dark:hover:bg-white/10"
              >
                <button
                  type="button"
                  onClick={() => onChange(p.slots)}
                  title={p.slots.map((s) => s.model).join(", ") || "Empty preset"}
                  className="min-w-0 flex-1 truncate px-2 py-1.5 text-left font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]"
                >
                  {p.name}
                  <span className="ml-1.5 text-xs text-[#8a8f98]">
                    ({p.slots.length})
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => deletePreset(p.name)}
                  aria-label={`Delete preset ${p.name}`}
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#fdecec] hover:text-[#b91c1c] dark:text-[#C3C2B7] dark:hover:bg-[#ef4444]/15 dark:hover:text-[#f87171]"
                >
                  ×
                </button>
              </div>
            ))}
            {presets.length === 0 && (
              <p className="px-3 py-1 font-sans text-xs text-[#8a8f98]">No presets yet.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
