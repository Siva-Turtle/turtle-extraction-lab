import * as React from "react";
import { toast } from "sonner";
import { api } from "../../lib/api";
import type { ModelEndpoint, ModelInfo } from "../../lib/api";
import { fmtCost, fmtINR, fmtTokensShort, shortModel } from "../../lib/format";
import type { ModelSlot } from "../../lib/logTypes";
import { effortOptionsFor } from "../../lib/models";
import { useIsNarrow } from "../../lib/useIsNarrow";
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
        provider:
          typeof (s as { provider?: unknown }).provider === "string"
            ? ((s as { provider?: string }).provider as string)
            : "",
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

// Module-level endpoints cache: model id -> endpoints (successful fetches).
const endpointsCache = new Map<string, ModelEndpoint[]>();
const endpointsFailed = new Set<string>();

function endpointLabel(e: ModelEndpoint): string {
  return e.quantization ? `${e.name} (${e.quantization})` : e.name;
}

function fmtPer1M(pricePerToken: number | null | undefined): string {
  if (typeof pricePerToken !== "number" || !Number.isFinite(pricePerToken)) return "—";
  return `$${(pricePerToken * 1_000_000).toFixed(2)}`;
}

function priceForSlot(
  slot: ModelSlot,
  models: ModelInfo[],
  endpointsByModel: Record<string, ModelEndpoint[]>,
): { prompt: number | null; completion: number | null } {
  const prov = (slot.provider ?? "").trim();
  if (prov) {
    const list = endpointsByModel[slot.model];
    if (Array.isArray(list)) {
      const hit = list.find((e) => e.slug === prov);
      if (hit && hit.pricing) {
        const p = hit.pricing.prompt;
        const c = hit.pricing.completion;
        if ((typeof p === "number" && Number.isFinite(p)) || (typeof c === "number" && Number.isFinite(c))) {
          return {
            prompt: typeof p === "number" && Number.isFinite(p) ? p : null,
            completion: typeof c === "number" && Number.isFinite(c) ? c : null,
          };
        }
      }
    }
  }
  const m = models.find((x) => x.id === slot.model);
  const pricing = m?.pricing ?? null;
  if (!pricing) return { prompt: null, completion: null };
  const p = pricing.prompt;
  const c = pricing.completion;
  return {
    prompt: typeof p === "number" && Number.isFinite(p) ? p : null,
    completion: typeof c === "number" && Number.isFinite(c) ? c : null,
  };
}

/**
 * Multi-model slot picker: selected slots as a table
 * (`# | Model | Effort | Provider | Price in/out per 1M | actions`),
 * plus an add-model combobox, a favourites row, and localStorage presets.
 */
export function ModelSlotsPicker({
  slots,
  onChange,
  models,
  max = 4,
  estimates,
}: {
  slots: ModelSlot[];
  onChange: (s: ModelSlot[]) => void;
  models: ModelInfo[];
  max?: number;
  estimates?: Record<string, { inTokens: number; outTokens: number; usd: number | null }>;
}): React.JSX.Element {
  // effortOptionsFor wants the /models meta shape; the live flag only gates
  // the "no reasoning options" disabled state, and a populated list means
  // the backend answered, so treat it as live.
  const meta = React.useMemo(() => ({ live: true, models }), [models]);
  const [presets, setPresets] = React.useState<Preset[]>(loadPresets);
  const [presetsOpen, setPresetsOpen] = React.useState(false);
  const [favTick, setFavTick] = React.useState(0);
  const narrow = useIsNarrow();
  const [endpointsByModel, setEndpointsByModel] = React.useState<Record<string, ModelEndpoint[]>>(
    () => {
      const init: Record<string, ModelEndpoint[]> = {};
      for (const s of slots) {
        const cached = endpointsCache.get(s.model);
        if (cached && !(s.model in init)) init[s.model] = cached;
      }
      return init;
    },
  );
  const [loadingModels, setLoadingModels] = React.useState<Record<string, boolean>>({});
  const inFlightRef = React.useRef<Set<string>>(new Set());

  // Favourites are starred inside the combobox dropdown (which owns its own
  // state), so refresh the row when the window regains focus.
  React.useEffect(() => {
    const refresh = () => setFavTick((t) => t + 1);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);

  // Lazily fetch provider endpoints per model when a row is added.
  React.useEffect(() => {
    const wanted = [...new Set(slots.map((s) => s.model).filter((m) => m !== ""))];
    for (const m of wanted) {
      const cached = endpointsCache.get(m);
      if (cached) {
        setEndpointsByModel((prev) => (prev[m] ? prev : { ...prev, [m]: cached }));
        continue;
      }
      if (inFlightRef.current.has(m) || endpointsFailed.has(m)) continue;
      inFlightRef.current.add(m);
      setLoadingModels((prev) => ({ ...prev, [m]: true }));
      api
        .get("/models/endpoints", { params: { model: m } })
        .then(
          (res) => {
            const data = res.data as { endpoints?: unknown };
            const raw = Array.isArray(data?.endpoints) ? (data.endpoints as unknown[]) : [];
            const clean: ModelEndpoint[] = [];
            for (const item of raw) {
              if (!item || typeof item !== "object") continue;
              const rec = item as Record<string, unknown>;
              const slug = typeof rec.slug === "string" ? rec.slug : "";
              const name = typeof rec.name === "string" ? rec.name : "";
              if (slug === "" || name === "") continue;
              const pricingRaw =
                rec.pricing && typeof rec.pricing === "object"
                  ? (rec.pricing as Record<string, unknown>)
                  : {};
              const prompt =
                typeof pricingRaw.prompt === "number" && Number.isFinite(pricingRaw.prompt)
                  ? (pricingRaw.prompt as number)
                  : null;
              const completion =
                typeof pricingRaw.completion === "number" &&
                Number.isFinite(pricingRaw.completion)
                  ? (pricingRaw.completion as number)
                  : null;
              clean.push({
                slug,
                name,
                tag: typeof rec.tag === "string" ? (rec.tag as string) : "",
                quantization:
                  typeof rec.quantization === "string" ? (rec.quantization as string) : null,
                context_length:
                  typeof rec.context_length === "number" &&
                  Number.isFinite(rec.context_length)
                    ? (rec.context_length as number)
                    : null,
                pricing: { prompt, completion },
                uptime_last_30m:
                  typeof rec.uptime_last_30m === "number" &&
                  Number.isFinite(rec.uptime_last_30m)
                    ? (rec.uptime_last_30m as number)
                    : null,
                status:
                  typeof rec.status === "number" && Number.isFinite(rec.status)
                    ? (rec.status as number)
                    : null,
              });
            }
            clean.sort((a, b) => a.name.localeCompare(b.name));
            endpointsCache.set(m, clean);
            setEndpointsByModel((prev) => ({ ...prev, [m]: clean }));
          },
          () => {
            endpointsFailed.add(m);
          },
        )
        .finally(() => {
          inFlightRef.current.delete(m);
          setLoadingModels((prev) => {
            if (!(m in prev)) return prev;
            const next = { ...prev };
            delete next[m];
            return next;
          });
        });
    }
  }, [slots]);

  const favourites = React.useMemo((): string[] => {
    void favTick;
    return loadFavouriteModels().filter(
      (f) => !slots.some((s) => s.model === f && s.effort === "" && (s.provider ?? "") === ""),
    );
  }, [favTick, slots]);

  const full = slots.length >= max;

  function addModel(id: string) {
    const clean = id.trim();
    if (clean === "") return;
    if (slots.length >= max) {
      toast.error(`Max ${max} models`);
      return;
    }
    if (slots.some((s) => s.model === clean && s.effort === "" && (s.provider ?? "") === "")) {
      toast.error("Already added");
      return;
    }
    onChange([...slots, { model: clean, effort: "", provider: "" }]);
  }

  function setEffort(index: number, effort: string) {
    const current = slots[index];
    if (!current) return;
    const prov = current.provider ?? "";
    if (
      slots.some(
        (s, i) => i !== index && s.model === current.model && s.effort === effort && (s.provider ?? "") === prov,
      )
    ) {
      toast.error("Already added");
      return;
    }
    onChange(slots.map((s, i) => (i === index ? { ...s, effort } : s)));
  }

  function setProvider(index: number, provider: string) {
    const current = slots[index];
    if (!current) return;
    const prov = provider ?? "";
    if (
      slots.some(
        (s, i) =>
          i !== index && s.model === current.model && s.effort === current.effort && (s.provider ?? "") === prov,
      )
    ) {
      toast.error("Already added");
      return;
    }
    onChange(slots.map((s, i) => (i === index ? { ...s, provider: prov } : s)));
  }

  function removeSlot(index: number) {
    onChange(slots.filter((_, i) => i !== index));
  }

  function duplicateSlot(index: number) {
    const current = slots[index];
    if (!current) return;
    if (slots.length >= max) {
      toast.error(`Max ${max} models`);
      return;
    }
    onChange([...slots, { ...current }]);
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
    <div className="grid min-w-0 max-w-full gap-2">
      <div
        className="max-w-full overflow-x-auto rounded-2xl border border-[#e5e7eb] bg-white dark:border-white/10 dark:bg-[#1a1a1a]"
        data-narrow={narrow ? "true" : "false"}
      >
        <table className="w-full min-w-[720px] text-left text-sm" aria-label="Selected models">
          <thead>
            <tr className="border-b border-[#e5e7eb] font-heading text-xs font-bold uppercase tracking-wide text-[#8a8f98] dark:border-white/10">
              <th className="px-4 py-3">#</th>
              <th className="px-4 py-3">Model</th>
              <th className="px-4 py-3">Effort</th>
              <th className="px-4 py-3">Provider</th>
              <th className="px-4 py-3">Price in / out per 1M</th>
              <th className="px-4 py-3">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {slots.length === 0 ? (
              <tr className="border-b border-[#e5e7eb] last:border-0 dark:border-white/10">
                <td colSpan={6} className="px-4 py-3 font-sans text-xs text-[#8a8f98]">
                  No models selected
                </td>
              </tr>
            ) : (
              slots.map((s, i) => {
                const effortInfo = effortOptionsFor(meta, s.model);
                const endpoints = endpointsByModel[s.model] ?? endpointsCache.get(s.model) ?? [];
                const loading = loadingModels[s.model] === true;
                const price = priceForSlot(s, models, endpointsByModel);
                const priceText =
                  price.prompt === null && price.completion === null
                    ? "—"
                    : `${fmtPer1M(price.prompt)} / ${fmtPer1M(price.completion)}`;
                const priceTitle =
                  price.prompt === null && price.completion === null
                    ? "Price unknown"
                    : `in ${fmtPer1M(price.prompt)} per 1M · out ${fmtPer1M(price.completion)} per 1M`;
                return (
                  <tr
                    key={`${s.model}|${s.effort}|${s.provider ?? ""}|${i}`}
                    className="border-b border-[#e5e7eb] last:border-0 dark:border-white/10"
                  >
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                      {i + 1}
                    </td>
                    <td className="max-w-56 px-4 py-3" title={s.model}>
                      <span className="block max-w-48 truncate font-mono text-xs text-[#1d1d1d] dark:text-[#F0EFEC]">
                        {shortModel(s.model)}
                      </span>
                      <span className="block max-w-48 truncate font-mono text-[11px] text-[#8a8f98]">
                        {s.model}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={s.effort}
                        onChange={(e) => setEffort(i, e.target.value)}
                        disabled={effortInfo.disabled}
                        aria-label={`Reasoning effort for ${s.model}`}
                        title={
                          effortInfo.disabled
                            ? "No reasoning options for this model"
                            : "Reasoning effort"
                        }
                        className="h-8 max-w-28 rounded-lg border border-[#e5e7eb] bg-white px-1.5 font-sans text-xs text-[#1d1d1d] disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC]"
                      >
                        <option value="">default</option>
                        {effortInfo.options.map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-4 py-3">
                      <select
                        value={s.provider ?? ""}
                        onChange={(e) => setProvider(i, e.target.value)}
                        aria-label={`Provider for ${s.model}`}
                        title={loading ? "Loading providers…" : "OpenRouter provider"}
                        className="h-8 max-w-44 rounded-lg border border-[#e5e7eb] bg-white px-1.5 font-sans text-xs text-[#1d1d1d] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC]"
                      >
                        <option value="">Auto (OpenRouter picks)</option>
                        {!loading &&
                          endpoints.map((e) => (
                            <option key={e.slug} value={e.slug} title={e.slug}>
                              {endpointLabel(e)}
                            </option>
                          ))}
                      </select>
                    </td>
                    <td
                      className="whitespace-nowrap px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]"
                      title={priceTitle}
                    >
                      {priceText}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <span className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => duplicateSlot(i)}
                          disabled={full}
                          aria-label={`Duplicate ${s.model}`}
                          title={full ? `Max ${max} models` : "Duplicate"}
                          className="flex h-6 w-6 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] hover:text-[#1d1d1d] disabled:cursor-not-allowed disabled:opacity-50 dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]"
                        >
                          ⧉
                        </button>
                        <button
                          type="button"
                          onClick={() => removeSlot(i)}
                          aria-label={`Remove ${s.model}`}
                          className="flex h-6 w-6 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#fdecec] hover:text-[#b91c1c] dark:text-[#C3C2B7] dark:hover:bg-[#ef4444]/15 dark:hover:text-[#f87171]"
                        >
                          ×
                        </button>
                      </span>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {slots.length > 0 && estimates && <CostHint slots={slots} estimates={estimates} />}
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
        </Button>{" "}
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
                  <span className="ml-1.5 text-xs text-[#8a8f98]">({p.slots.length})</span>
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

/**
 * One-line run cost estimate under the table:
 * `≈ ₹X total · ₹a–₹b per model · ~Nk input tokens (estimate)`.
 * Hidden when no slot has pricing data; notes "price unknown" for models
 * without pricing. INR first, USD in `title`.
 */
function CostHint({
  slots,
  estimates,
}: {
  slots: ModelSlot[];
  estimates: Record<string, { inTokens: number; outTokens: number; usd: number | null }>;
}): React.JSX.Element | null {
  const known: number[] = [];
  let unknown = 0;
  let inTokens = 0;
  for (const s of slots) {
    const e =
      estimates[`${s.model}|${s.effort}|${s.provider ?? ""}`] ??
      estimates[`${s.model}|${s.effort}`];
    if (!e) {
      unknown += 1;
      continue;
    }
    if (typeof e.usd === "number" && Number.isFinite(e.usd)) known.push(e.usd);
    else unknown += 1;
    if (typeof e.inTokens === "number" && Number.isFinite(e.inTokens) && e.inTokens > inTokens) {
      inTokens = e.inTokens;
    }
  }
  if (known.length === 0) return null;
  const total = known.reduce((a, b) => a + b, 0);
  const lo = Math.min(...known);
  const hi = Math.max(...known);
  const perModel = known.length === 1 || lo === hi ? fmtINR(lo) : `${fmtINR(lo)}–${fmtINR(hi)}`;
  const text =
    `≈ ${fmtINR(total)} total · ${perModel} per model · ` +
    `${fmtTokensShort(inTokens)} input tokens (estimate)` +
    (unknown > 0 ? ` · price unknown for ${unknown} model${unknown === 1 ? "" : "s"}` : "");
  const title =
    `Estimated cost ${fmtCost(total)} USD total` +
    (known.length === 1 || lo === hi ? "" : ` (${fmtCost(lo)}–${fmtCost(hi)} USD per model)`) +
    (unknown > 0 ? "; some models have no pricing" : "") +
    " — estimate only";
  return (
    <p title={title} className="font-sans text-xs text-[#4a5058] dark:text-[#C3C2B7]">
      {text}
    </p>
  );
}
