// Cost estimate for a multi-model run (client-side hint only).
//
// Input tokens per agent = (transcript chars + agent system prompt chars) / 4.
// Output tokens per agent = attribute count x 60.
// Totals are summed across agents; cost uses the model's per-token pricing
// (USD/token). `usd` is null when either price is null/missing.

export type EstimatePricing = {
  prompt: number | null;
  completion: number | null;
} | null | undefined;

export type EstimateInput = {
  transcriptChars: number;
  agentPrompts: { systemChars: number; attrCount: number }[];
  pricing: EstimatePricing;
};

export type RunEstimate = {
  inTokens: number;
  outTokens: number;
  usd: number | null;
};

export function estimateRunCost(input: EstimateInput): RunEstimate {
  const transcriptChars =
    typeof input.transcriptChars === "number" && Number.isFinite(input.transcriptChars)
      ? Math.max(0, input.transcriptChars)
      : 0;
  const prompts = Array.isArray(input.agentPrompts) ? input.agentPrompts : [];
  let inTokens = 0;
  let outTokens = 0;
  for (const p of prompts) {
    const sys =
      p && typeof p.systemChars === "number" && Number.isFinite(p.systemChars)
        ? Math.max(0, p.systemChars)
        : 0;
    const attrs =
      p && typeof p.attrCount === "number" && Number.isFinite(p.attrCount)
        ? Math.max(0, Math.floor(p.attrCount))
        : 0;
    inTokens += (transcriptChars + sys) / 4;
    outTokens += attrs * 60;
  }
  const pricing = input.pricing ?? null;
  // Negative catalog prices ("-1" for dynamic routers) are unknown, never a price.
  const promptPrice =
    pricing && typeof pricing.prompt === "number" && Number.isFinite(pricing.prompt) && pricing.prompt >= 0
      ? pricing.prompt
      : null;
  const completionPrice =
    pricing && typeof pricing.completion === "number" && Number.isFinite(pricing.completion) && pricing.completion >= 0
      ? pricing.completion
      : null;
  const usd =
    promptPrice === null || completionPrice === null
      ? null
      : inTokens * promptPrice + outTokens * completionPrice;
  return {
    inTokens: Math.round(inTokens),
    outTokens: Math.round(outTokens),
    usd,
  };
}
