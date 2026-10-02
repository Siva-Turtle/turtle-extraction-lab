import { REASONING_EFFORTS } from "./api";
import type { ModelReasoning } from "./api";

export type ModelsMeta = {
  live: boolean;
  models: { id: string; reasoning?: ModelReasoning | null }[];
};

export type EffortOption = { value: string; label: string };

/**
 * Per-model reasoning-effort options (moved out of TestLab.tsx unchanged in
 * behaviour; OpenRouter docs: GET /models entries MAY carry `reasoning`;
 * supported_efforts is descending, null = all gateway efforts accepted,
 * omitted = no effort selection exposed).
 */
export function effortOptionsFor(
  modelsMeta: ModelsMeta | undefined | null,
  modelId: string,
): { options: EffortOption[]; disabled: boolean; placeholder: string } {
  const id = modelId.trim();
  const entry = modelsMeta?.models.find((m) => m.id === id);
  const live = modelsMeta?.live ?? false;
  const info: ModelReasoning | null = entry?.reasoning ?? null;
  const supported: string[] | null = Array.isArray(info?.supported_efforts)
    ? (info.supported_efforts as unknown[]).filter(
        (v): v is string => typeof v === "string" && v.trim() !== "",
      )
    : null;
  const mandatory = info?.mandatory === true;
  const options = (supported ?? [...REASONING_EFFORTS])
    .filter((v) => !(mandatory && v === "none"))
    .map((v) => ({ value: v, label: v }));
  const disabled = id === "" || (live && !!entry && !info);
  const placeholder =
    id === ""
      ? "Select a model first"
      : disabled
        ? "No reasoning options for this model"
        : "Default (no effort)";
  return { options, disabled, placeholder };
}
