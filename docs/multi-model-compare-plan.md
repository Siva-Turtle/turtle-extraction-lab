# Multi-model compare + thumbs feedback — design plan

Status: approved defaults pending Siva's answers to section D. Author: design planner agent, 3 Oct 2026.

## Summary
Keep "one log row = one model call". A multi-model run = N ordinary `POST /runs` calls sharing a new `run_group_id`, fired in parallel by the browser (max 3 concurrent, max 6 models), so each model's column fills in as it finishes. Thumbs (per-attribute up/down + remarks) stay; add a per-model overall verdict, clearing, and batch/linked rating. One shared `ComparisonMatrix` component is used by Test Lab and Log detail. One model => today's single-model table (`SingleModelTable`).

## A. UX

### A1 ModelSlotsPicker (replaces Model + Reasoning effort fields)
- Slot = `{model, effort}`; same model twice with different effort allowed; duplicate pairs blocked.
- Chips: short name (strip vendor prefix, full id tooltip), effort dropdown (model's supported_efforts, else disabled "default"), remove x.
- Add field = ModelCombobox in "add" mode (pick adds chip and clears input; favourites first; custom ids allowed).
- Favourites row: one click adds a starred model.
- Presets in localStorage `lab:model-presets` = `[{name, slots}]`: save current, load (replaces), delete.
- Last selection in `lab:last-model-slots`. Empty first time; Run disabled "Add a model first".
- Max 6 slots; 3 run concurrently, rest queued.
- Cost hint (estimate): input tokens per agent = (scrubbed transcript chars + agent system prompt chars from `GET /agents/{id}/prompt-preview`)/4; output = attrs x 60; prices from `/models` `pricing`. Show INR first, USD tooltip; "price unknown" if missing.
- Button: "Run" or "Run N models"; Ctrl+Enter runs.

### A2 Progressive results (Test Lab)
- Results card appears on Run; columns in picker order, never reorder.
- Column status: queued (grey), running (spinner + elapsed timer), done, partial (amber, some agents `_error`), error (red).
- Skeleton cells until column finishes; rows from selected agents + prompt-preview attributes.
- Failed column: error text (truncated + details) + Retry (same slot, same group id). Agent failed for a model: single red cell "Agent failed · Retry model".
- Toast at end: "3/3 models done" / "2/3 done · 1 failed". Header link "Open in Logs".

### A3 ComparisonMatrix
Rows = attributes grouped under agent section headers; columns = models.
- Toolbar: filter chips with counts All / Disagreements / Unrated / Rated-down; toggles "Merge identical" (default on) and "Link identical" (default on); Detail switch Value | +Confidence | +Evidence; shortcut "?" ; tally up/down.
- Sticky left attribute column: name + agreement mark (✓ unanimous, ≠ k/N majority, ⚡ split, ∅ none found); tooltip = description.
- Sticky top `ModelColumnHeader`: short name, effort badge, cost (INR, USD tooltip), wall time, tokens in→out (+thought), agrees-% with consensus, up/down tally, "rated k/M", badges cheapest/fastest/most-up when ≥2 columns, Overall thumbs, ⋯ menu (thumbs-up all unrated, thumbs-down all unrated, clear my ratings in column, retry, view raw, copy JSON).
- `CompareCell`: value first clamped to 3 lines; arrays as chips (4 then +n); objects as k: v lines; optional confidence/evidence line; small thumbs right-aligned; remarks dot.
- Tints by priority: rated down (red left border + #fdecec), rated up (green left border), disagrees with majority (amber #fef6e7 + amber bar), split row (info #eaf1fe), not found (muted italic "— not found"), unanimous (no tint). Once a cell in a row is up, cells with a different value get small "≠ accepted" marker.
- Merge identical: unanimous row = one cell spanning all columns with one thumbs pair that rates all N.
- Link identical: rating a cell also rates same-valued cells in the row; toast "Applied 👍 to 3 models" with Undo.
- Agent sections collapsible; header shows its disagreement count.
- `AttributeCompareDrawer` (click value / Enter): description + type, then per model: full value (object key/value table, differing keys highlighted), confidence, evidence, thumbs, editable remarks. Reuse `Drawer`.
- Remarks popover (R): input, Save, Esc.
- Scroll container `max-h-[calc(100svh-140px)] overflow-auto` so sticky header and column both work; column min width 220px; horizontal scroll beyond 4 columns.
- Identifier agent row `selected_agents`, value as badges, compared as a set.

### A4 Keyboard (role="grid", roving tabindex)
Arrows move; U/D rate focused cell (link applies) then move down; X or 0 clear; R remarks; Enter/Space drawer; Shift+U/Shift+D rate all unrated in column; N next unrated (disagreements first); ? shortcut sheet (Modal). Ignore when focus is in input/textarea.

### A5 Single model
One column => `SingleModelTable` (today's Logs PrettyPanel per-agent table: AttrRow + IdentifierFeedback, moved unchanged). Test Lab single-model uses it too (save-on-click thumbs), replacing pick-then-Save RatingBox.

### A6 Log history list
One row per group (old rows without group id = one-row groups). Columns: Date | Models (≤3 short badges +n, tooltip with per-model cost) | Effort ("mixed" if differs) | Input | Agents | Agree % (client-side) | Ratings (👍n 👎n) | Cost (sum) | Tokens | Time (max). Single-model groups look like today. Optional caret to expand per-model sub-rows. Model filter server-side (group shows if any model matches; detail loads whole group; note "showing 2 of 3 models"). Optional client-side "Run kind: single / comparison" filter.

### A7 Log detail
Keep Pretty / Raw / Analytics tabs. Fetch `GET /logs?run_group_ids=<gid>`. Pretty: Meeting block + collapsible Input as today, then ComparisonMatrix (editable) or SingleModelTable. Raw: model segmented control. Analytics: new "Model comparison" table (model, effort, in, out, thought, input cost, output cost, cost, time, agree %, 👍, 👎, overall) + existing per-agent table merged from all members; stat tiles = group totals.

### A8 Narrow (<768px)
`CompareCardList`: one card per attribute (name + agreement mark), one line per model (short name, value, thumbs); merged unanimous row = "All 3 models" one line. Model summaries become horizontal strip of compact headers. Filters stay; no keyboard shortcuts.

### A9 States
No models -> Run disabled w/ reason; all queued -> skeleton; all failed -> red banner w/ each error + "Retry all"; agent returned nothing -> "— not found"; group fetch failed -> error card + Retry; pricing unknown -> "—", no cheapest badge; old rows -> one-row groups, overall blank.

### A10 Components
- `frontend/src/components/ui/ThumbButtons.tsx` `{value: "up"|"down"|null, onChange(next|null), size?: "sm"|"md", disabled?}`
- `frontend/src/components/ui/RemarksPopover.tsx` `{value, onSave(text), onClose}`
- `frontend/src/components/run/ModelSlotsPicker.tsx` `{slots, onChange, max?: 6, estimate?}` (+ CostHint)
- `frontend/src/components/compare/ComparisonMatrix.tsx` `{columns: CompareColumn[], agents: CompareAgent[], editable, onRetry?(slotKey), focusColumnKey?}`
- `frontend/src/components/compare/ModelColumnHeader.tsx`, `CompareCell.tsx`, `CompareToolbar.tsx`, `AttributeCompareDrawer.tsx`, `CompareCardList.tsx`, `SingleModelTable.tsx`
- `frontend/src/lib/compare.ts` (pure), `useFeedback.ts`, `useMultiRun.ts`, `logTypes.ts`, `format.ts`

## B. Data / API contract

### B1 Migration `0012_runlog_group_overall` (run_logs only, no FKs)
- `run_group_id` String(36) not null server_default "" + index `ix_run_logs_run_group_id`
- `overall_rating` String(16) not null server_default "" ("" | "up" | "down")
- `overall_remarks` Text not null server_default ""
Read rule: group key = run_group_id or, if "", the log's own id.

### B2 `POST /api/v1/runs` (backward compatible)
- `RunCreate.run_group_id: str = ""`, must match `^[0-9a-f]{32}$` or be blank, else 422. Frontend generates `crypto.randomUUID().replaceAll("-", "")`.
- Response adds `log_id`, `run_group_id`, `log` (full `_out(log)` dict, same shape as GET /logs items). Keep existing keys.
- No batch endpoint; frontend makes N parallel calls.

### B3 `GET /api/v1/logs`
- `_out` adds `run_group_id`, `overall_rating`, `overall_remarks`.
- New repeatable query `run_group_ids` (also `run_group_ids[]`): SQL filter before the 500 limit, matching `RunLog.run_group_id IN (...) OR RunLog.id IN (...)`.
- Raise return cap 100 -> 300.

### B4 Feedback
1. `POST /runs/{run_id}/feedback`: `rating` also accepts "" = remove that attribute entry (drop empty agent key), still write Feedback history row with rating "clear". `remarks: str | None = None` — None keeps existing remarks, string replaces.
2. New `POST /runs/feedback-batch` (declare before `/{run_id}` routes): `{items: [{run_id, agent_name, attribute_name, rating: "up"|"down"|"", remarks: str|null}], only_unrated: bool=false}` -> `{ok, applied, skipped}`; one transaction; only_unrated skips already-rated cells; unknown run_id -> 404, apply nothing.
3. New `POST /runs/{run_id}/overall`: `{rating: "up"|"down"|"", remarks?: str|null}` -> sets overall_rating/overall_remarks; `{ok: true}`.

### B5 `GET /api/v1/models`
Each live entry adds `pricing: {prompt: float|null, completion: float|null}` (USD/token, same `_parse_price`); fallback entries `pricing: null`; refill `_pricing_cache` from the same response.

### B6 Frontend types (`frontend/src/lib/logTypes.ts`)
```ts
export type Rating = "up" | "down";
export type FeedbackEntry = { rating: Rating | ""; remarks: string };
export type FeedbackMap = Record<string, Record<string, FeedbackEntry>>;
export type LogRow = { /* existing */ run_group_id: string; overall_rating: "" | Rating; overall_remarks: string };
export type ModelSlot = { model: string; effort: string }; // key `${model}|${effort}`
export type ColumnStatus = "queued" | "running" | "done" | "partial" | "error";
export type CompareColumn = { key: string; model: string; effort: string; status: ColumnStatus; startedAt?: number; error?: string; log: LogRow | null };
export type CompareAgent = { id: string; name: string; kind: string; attributes: { name: string; type: string; description: string; group: string }[] };
```

### B7 `compare.ts`
- `canonicalKey(value, confidenceType)`: "∅" if null/undefined/"" or confidence type not_found; strings trimmed, whitespace collapsed, lowercased; numbers Number; arrays canonicalised + sorted + JSON; objects sorted keys; booleans as is.
- `buildRows(agents, columns)` -> per agent × attribute `{agentId, agentName, attr, cells: Record<colKey, CellState>, groups: Map<canon, colKey[]>, state: "unanimous"|"majority"|"split"|"none_found"|"pending", consensusKey?}`; cell `{value, confidence, confidenceType, evidence, canon, error?, feedback}`. Order from snapshot then extra output keys. Compare only done/partial columns whose agent has no `_error`. unanimous = 1 group (∅ => none_found); majority = largest group > half; else split.
- `columnStats(rows, colKey)` -> `{agreePct, up, down, rated, total}`.
- `linkedTargets(rows, row, colKey)` -> other columns in same canonical group.
- Group agreement % for list = mean over rows of largest group / compared count.

## C. Phases
- Phase 0 (refactor, no behaviour change): T0.1 logTypes.ts + format.ts (+shortModel); T0.2 ThumbButtons; T0.3 Drawer `closeLabel?`; T0.4 SingleModelTable extracted from Logs PrettyPanel.
- Phase 1 (backend): T1.1 migration + model + _out; T1.2 run endpoint group id + log in response; T1.3 group filter + cap 300; T1.4 feedback clear / optional remarks / batch / overall; T1.5 pricing on /models.
- Phase 2 (Test Lab): T2.1 ModelSlotsPicker; T2.2 useMultiRun; T2.3 compare.ts; T2.4 read-only matrix; T2.5 useFeedback + thumbs in matrix; T2.6 keyboard; T2.7 cell drawer; T2.8 cost hint; T2.9 narrow card list.
- Phase 3 (Logs): T3.1 grouped list; T3.2 group detail; T3.3 optional extras; T3.4 shared data source.

## Decisions (Siva, 3 Oct 2026) — these OVERRIDE anything above
- Link identical: ON by default, with Undo toast and toolbar toggle.
- NO thumbs in model column headers: no overall per-model verdict (drop `overall_rating`/`overall_remarks` columns and `POST /runs/{run_id}/overall`), no column bulk-rate actions in the ⋯ menu, no Shift+U/Shift+D. Column header keeps metrics + up/down tally + retry/raw/copy JSON. Thumbs only on cells (and merged cells).
- Test Lab single-model: switch to SingleModelTable (save-on-click thumbs).
- Limit: max 4 models per run, all 4 run at once (no queue).
- Defaults kept for: loose value equality; retry shows latest attempt per model+effort; USD_TO_INR = 100 in format.ts.

## D. Open questions (recommended default in brackets)
1. Test Lab single model uses save-on-click table? [yes]
2. Overall per-model verdict stored + bulk "thumbs-up all unrated"? [both]
3. Link identical on by default? [on, with Undo]
4. Limit? [6 models, 3 concurrent]
5. Value equality? [loose: case/whitespace/array order ignored]
6. Retry in group? [matrix shows latest attempt per model+effort; older in Raw]
7. USD_TO_INR = 100 hard-coded? [keep, move to format.ts]
