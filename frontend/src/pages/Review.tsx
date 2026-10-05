import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Pencil, Scale, Star } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";
import { serverDetail } from "../lib/format";
import { IDENTIFIER_QUESTION_KEYS } from "../lib/format";
import type { LogRow } from "../lib/logTypes";
import {
  batchDisplayName,
  buildRows,
  chunkText,
  clusterBatches,
  fetchStars,
  isStarred,
  loadBatchNames,
  loadBatchSel,
  pairGroups,
  persistStars,
  saveBatchNames,
  saveBatchSel,
  toggleStar,
} from "../lib/review";
import type { ReviewBatch, ReviewRow, ReviewSide } from "../lib/review";
import { useFeedback } from "../lib/useFeedback";
import { useIsNarrow } from "../lib/useIsNarrow";
import { cn } from "../lib/cn";
import { PageHeader } from "../components/ui/PageHeader";
import { Card } from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { ThumbButtons } from "../components/ui/ThumbButtons";
import { RemarksPopover } from "../components/ui/RemarksPopover";
import { SingleSelectFilter } from "../components/ui/Combobox";

type Mode = "discrepancies" | "all" | "starred";

const MODE_LABELS: Record<Mode, string> = {
  discrepancies: "Discrepancies",
  all: "All",
  starred: "Starred",
};

/** Browser-local bookmark toggle for one review row (★ fills amber). */
function StarButton({
  starred,
  onToggle,
}: {
  starred: boolean;
  onToggle: () => void;
}): React.JSX.Element {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      aria-pressed={starred}
      aria-label={starred ? "Unstar row" : "Star row"}
      title={starred ? "Unstar row" : "Star row"}
      className={cn(
        "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border transition-colors",
        starred
          ? "border-amber-400 bg-amber-100 text-amber-500 dark:border-amber-400/60 dark:bg-amber-400/15 dark:text-amber-300"
          : "border-[#e5e7eb] text-[#8a8f98] hover:border-amber-400 hover:text-amber-500 dark:border-white/10 dark:hover:border-amber-400/60",
      )}
    >
      <Star
        className={cn("h-4 w-4", starred && "fill-amber-400 text-amber-500 dark:fill-amber-300 dark:text-amber-300")}
        aria-hidden="true"
      />
    </button>
  );
}

/** Muted "—" placeholder for missing data (never crashes on old logs). */
function Dash({ label = "—" }: { label?: string }): React.JSX.Element {
  return <span className="text-[#8a8f98]">{label}</span>;
}

function BoolMark({ value }: { value: boolean | null }): React.JSX.Element {
  if (value === null) return <Dash />;
  return (
    <span
      aria-label={value ? "yes" : "no"}
      className={cn(
        "inline-flex h-6 w-6 items-center justify-center rounded-full text-sm",
        value
          ? "bg-[#e9f9ef] text-[#15803d] dark:bg-[#22c55e]/15 dark:text-[#4ade80]"
          : "bg-[#fdecec] text-[#b91c1c] dark:bg-[#ef4444]/15 dark:text-[#f87171]",
      )}
    >
      {value ? "✅" : "❌"}
    </span>
  );
}

/**
 * One model cell: ✅/❌ value, noul probability (Jev shows the prob line
 * always, muted when null; Opus omits it), thumbs underneath with a remarks
 * dot + 💬 button (CompareCell pattern). Remarks popover anchors to the
 * relative wrapper.
 */
function ModelCell({
  side,
  attr,
  showProb,
  feedback,
  remarksFor,
  onRemarksToggle,
  onRemarksClose,
  onRemarksSave,
}: {
  side: ReviewSide;
  attr: string;
  showProb: boolean;
  feedback: ReturnType<typeof useFeedback>;
  remarksFor: string | null;
  onRemarksToggle: (cellKey: string) => void;
  onRemarksClose: () => void;
  onRemarksSave: (runId: string, agent: string, attr: string, text: string) => void;
}): React.JSX.Element {
  const cellKey = `${side.runId}|${side.agentName}|${attr}`;
  const rating = side.runId
    ? feedback.getRating(side.runId, side.agentName, attr, side.feedback)
    : null;
  const remarks = side.runId
    ? feedback.getRemarks(side.runId, side.agentName, attr, side.feedback)
    : "";
  const hasRemarks = remarks.trim() !== "";
  const open = remarksFor === cellKey;

  function handleRate(next: "up" | "down" | null): void {
    if (side.runId === "") return;
    const value: "up" | "down" | "" = next ?? "";
    void feedback
      .rate([{ runId: side.runId, agent: side.agentName, attr, rating: value }])
      .catch(() => undefined);
  }

  return (
    <div className="relative flex min-w-0 flex-col gap-1.5">
      <span className="flex items-center gap-1.5">
        <BoolMark value={side.value} />
        {showProb && (
          <span
            className={cn(
              "font-mono text-[11px]",
              typeof side.prob === "number" && Number.isFinite(side.prob)
                ? "text-[#4a5058] dark:text-[#C3C2B7]"
                : "text-[#8a8f98]",
            )}
            title={typeof side.prob === "number" ? `noul probability ${side.prob}` : "no probability recorded"}
          >
            {typeof side.prob === "number" && Number.isFinite(side.prob)
              ? `p ${side.prob.toFixed(2)}`
              : "p —"}
          </span>
        )}
      </span>
      <span className="flex items-center gap-1.5">
        <ThumbButtons value={rating} onChange={handleRate} size="sm" disabled={side.runId === ""} />
        {hasRemarks && (
          <span
            title="Has remarks"
            aria-label="Has remarks"
            className="h-1.5 w-1.5 rounded-full bg-[#1d1d1d] dark:bg-[#F0EFEC]"
          />
        )}
        <button
          type="button"
          onClick={() => onRemarksToggle(cellKey)}
          disabled={rating === null || side.runId === ""}
          title={rating === null ? "Rate first" : "Edit remarks"}
          aria-label="Edit remarks"
          aria-pressed={open}
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-full border border-[#e5e7eb] text-xs text-[#4a5058] transition-opacity hover:border-[#1d1d1d] disabled:cursor-not-allowed disabled:opacity-40 dark:border-white/10 dark:text-[#C3C2B7]",
            hasRemarks || open
              ? "opacity-100"
              : "opacity-0 focus-visible:opacity-100 group-hover/row:opacity-100 group-focus-within/row:opacity-100",
          )}
        >
          💬
        </button>
      </span>
      {open && (
        <RemarksPopover
          value={remarks}
          onClose={onRemarksClose}
          onSave={(text) => onRemarksSave(side.runId, side.agentName, attr, text)}
        />
      )}
    </div>
  );
}

/** Chunk-number cell ("—" when the side cites none). */
function EvNum({ n }: { n: number | null }): React.JSX.Element {
  if (n === null) return <Dash />;
  return <span className="font-mono text-xs text-[#1d1d1d] dark:text-[#F0EFEC]">#{n}</span>;
}

/** Evidence text: clamped to ~4 lines with expand; both chunks shown labeled
 * when the models cite different chunks. */
function EvidenceCell({ row }: { row: ReviewRow }): React.JSX.Element {
  const [expanded, setExpanded] = React.useState(false);
  const jevN = row.jev.evidence;
  const opusN = row.opus.evidence;
  if (jevN === null && opusN === null) return <Dash />;
  const blocks: { label: string; text: string }[] = [];
  if (jevN !== null && opusN !== null && jevN === opusN) {
    blocks.push({ label: `#${jevN}`, text: chunkText(row.chunks, jevN) });
  } else {
    if (jevN !== null) blocks.push({ label: `Jev #${jevN}`, text: chunkText(row.chunks, jevN) });
    if (opusN !== null) blocks.push({ label: `Opus #${opusN}`, text: chunkText(row.chunks, opusN) });
  }
  return (
    <div className="grid min-w-0 gap-1.5">
      {blocks.map((b) => (
        <div key={b.label} className="min-w-0">
          <p className="font-mono text-[10px] font-bold uppercase tracking-wide text-[#8a8f98]">
            {b.label}
          </p>
          {b.text !== "" ? (
            <p
              title={b.text}
              className={cn(
                "min-w-0 break-words font-sans text-xs italic text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]",
                !expanded && "line-clamp-4",
              )}
            >
              “{b.text}”
            </p>
          ) : (
            <p className="font-sans text-xs italic text-[#8a8f98]">chunk text missing</p>
          )}
        </div>
      ))}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-fit font-heading text-[11px] font-bold text-[#0d5c4a] hover:underline focus-visible:outline-2 focus-visible:outline-brand dark:text-[#5ee8cf]"
      >
        {expanded ? "Show less" : "Show more"}
      </button>
    </div>
  );
}

/** Fireflies link icon only; hidden when the URL is empty. */
function FirefliesLink({ url }: { url: string }): React.JSX.Element | null {
  if (url.trim() === "") return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      title="Open Fireflies transcript"
      aria-label="Open Fireflies transcript"
      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-[#e5e7eb] text-[#4a5058] transition-colors hover:border-[#1d1d1d] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:border-white/10 dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]"
    >
      <ExternalLink className="h-4 w-4" aria-hidden="true" />
    </a>
  );
}

export default function Review(): React.JSX.Element {
  const narrow = useIsNarrow();
  const feedback = useFeedback();
  const [mode, setMode] = React.useState<Mode>("discrepancies");
  const [question, setQuestion] = React.useState("all");
  const [batchId, setBatchId] = React.useState<string | null>(
    () => loadBatchSel()?.batchId ?? null,
  );
  const [batchNames, setBatchNames] = React.useState<Record<string, string>>(
    () => loadBatchNames(),
  );
  const [stars, setStars] = React.useState<string[]>([]);
  const [starsLoading, setStarsLoading] = React.useState(true);
  const [remarksFor, setRemarksFor] = React.useState<string | null>(null);

  // Server-persisted stars: load once on mount. The rest of the UI stays
  // usable while loading; failure toasts with a retry click.
  const loadStarsFromServer = React.useCallback(async () => {
    setStarsLoading(true);
    try {
      setStars(await fetchStars());
    } catch {
      toast.error("stars unavailable, retry", {
        action: { label: "Retry", onClick: () => void loadStarsFromServer() },
      });
    } finally {
      setStarsLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void loadStarsFromServer();
  }, [loadStarsFromServer]);

  const logsQuery = useQuery({
    queryKey: ["logs-all"],
    queryFn: async () => (await api.get("/logs")).data as LogRow[],
    staleTime: 30 * 1000,
  });
  const logs = logsQuery.data ?? [];
  const groups = React.useMemo(() => pairGroups(logs), [logs]);
  const batches = React.useMemo(() => clusterBatches(groups), [groups]);

  // Drop a persisted batch once the fetched groups no longer contain it.
  React.useEffect(() => {
    if (groups.length === 0 || batchId === null) return;
    if (!batches.some((b) => b.id === batchId)) setBatchId(null);
  }, [groups.length, batches, batchId]);

  // Persist where Siva left off (selected batch id; All = null).
  React.useEffect(() => {
    saveBatchSel({ batchId });
  }, [batchId]);

  React.useEffect(() => {
    saveBatchNames(batchNames);
  }, [batchNames]);

  const batchGroupIds = React.useMemo(() => {
    if (batchId === null) return null;
    const found = batches.find((b) => b.id === batchId);
    return found ? new Set(found.groupIds) : new Set<string>();
  }, [batches, batchId]);

  // Batch chip selects ALL of that batch's groups; All = every group.
  const selectedGroups = React.useMemo(
    () =>
      batchGroupIds === null
        ? groups
        : groups.filter((g) => batchGroupIds.has(g.id)),
    [groups, batchGroupIds],
  );

  const starredSet = React.useMemo(() => new Set(stars), [stars]);
  const allRows = React.useMemo(() => buildRows(selectedGroups), [selectedGroups]);
  const rows = React.useMemo(() => {
    let out = allRows;
    if (mode === "discrepancies") out = out.filter((r) => !r.agree);
    else if (mode === "starred") out = out.filter((r) => starredSet.has(r.key));
    if (question !== "all") out = out.filter((r) => r.questionKey === question);
    return out;
  }, [allRows, mode, question, starredSet]);

  // Rated-progress tally over the filtered rows (2 rateable cells per row).
  const rated = React.useMemo(() => {
    let n = 0;
    for (const r of rows) {
      for (const side of [r.jev, r.opus] as ReviewSide[]) {
        if (side.runId === "") continue;
        const cur = feedback.getRating(side.runId, side.agentName, r.questionKey, side.feedback);
        if (cur === "up" || cur === "down") n += 1;
      }
    }
    return n;
  }, [rows, feedback]);
  const totalCells = rows.length * 2;

  function toggleStarKey(key: string): void {
    const next = toggleStar(stars, key);
    setStars(next);
    void persistStars(next).catch(() => {
      // Roll back just this toggle; other concurrent star changes survive.
      setStars((cur) => toggleStar(cur, key));
      toast.error("Could not save stars");
    });
  }

  function renameBatch(batch: ReviewBatch): void {
    try {
      const current = batchDisplayName(batch, batchNames);
      const next = window.prompt("Rename batch", current);
      if (next === null) return;
      const t = next.trim();
      setBatchNames((prev) => {
        const copy = { ...prev };
        if (t === "" || t === batch.name) delete copy[batch.id];
        else copy[batch.id] = t;
        return copy;
      });
    } catch {
      // ignore (no window / prompt blocked)
    }
  }

  function saveRemarksForCell(runId: string, agent: string, attr: string, text: string): void {
    if (runId === "") return;
    // Current rating from the optimistic overlay (button is disabled until
    // rated, so this is up/down in practice).
    const rating = feedback.getRating(runId, agent, attr, null);
    void feedback
      .saveRemarks(runId, agent, attr, rating ?? "", text)
      .then(() => setRemarksFor(null))
      .catch(() => undefined);
  }

  const questionOptions = React.useMemo(
    () => [
      { value: "all", label: "All questions" },
      ...IDENTIFIER_QUESTION_KEYS.map((k) => ({ value: k, label: k })),
    ],
    [],
  );

  return (
    <div className="grid gap-4">
      <PageHeader
        title="Evidence review"
        actions={
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-[#f1f2f3] px-2.5 py-1 font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:bg-white/10 dark:text-[#C3C2B7]">
              <Scale className="h-3.5 w-3.5" aria-hidden="true" />
              Jev vs Opus · rated {rated}/{totalCells}
            </span>
            <span
              title="Starred rows"
              className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-1 font-heading text-[11px] font-bold uppercase tracking-wide text-amber-700 dark:bg-amber-400/15 dark:text-amber-300"
            >
              {starsLoading ? "★ …" : `★ ${stars.length}`}
            </span>
          </span>
        }
      />

      <Card>
        <div
          role="group"
          aria-label="Batch filter"
          className="flex flex-wrap items-center gap-1.5"
        >
          <button
            type="button"
            aria-pressed={batchId === null}
            onClick={() => setBatchId(null)}
            title="Show all batches"
            className={cn(
              "rounded-full px-4 py-2 font-heading text-xs font-bold transition-colors",
              batchId === null
                ? "bg-[#1d1d1d] text-white dark:bg-[#F0EFEC] dark:text-[#1d1d1d]"
                : "bg-[#f1f2f3] text-[#4a5058] hover:text-[#1d1d1d] dark:bg-white/5 dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]",
            )}
          >
            All · {groups.length}
          </button>
          {batches.map((b, i) => {
            const active = batchId === b.id;
            const latest = i === batches.length - 1;
            const label = batchDisplayName(b, batchNames);
            return (
              <span key={b.id} className="inline-flex items-center gap-1">
                <button
                  type="button"
                  aria-pressed={active}
                  onClick={() => setBatchId(b.id)}
                  title={`${label} — ${b.groupIds.length} group${b.groupIds.length === 1 ? "" : "s"}${latest ? " (newest)" : ""}`}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-4 py-2 font-heading text-xs font-bold transition-colors",
                    active
                      ? "bg-[#1d1d1d] text-white dark:bg-[#F0EFEC] dark:text-[#1d1d1d]"
                      : "bg-[#f1f2f3] text-[#4a5058] hover:text-[#1d1d1d] dark:bg-white/5 dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]",
                  )}
                >
                  {label} · {b.groupIds.length}
                  {latest && <Badge tone="brand">latest</Badge>}
                </button>
                <button
                  type="button"
                  onClick={() => renameBatch(b)}
                  title={`Rename ${label}`}
                  aria-label={`Rename ${label}`}
                  className="flex h-7 w-7 items-center justify-center rounded-full border border-[#e5e7eb] text-[#8a8f98] transition-colors hover:border-[#1d1d1d] hover:text-[#1d1d1d] dark:border-white/10 dark:hover:border-[#F0EFEC] dark:hover:text-[#F0EFEC]"
                >
                  <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </span>
            );
          })}
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Row filter"
            className="flex items-center gap-1 rounded-full border border-[#e5e7eb] bg-[#f1f2f3] p-1 dark:border-white/10 dark:bg-white/5"
          >
            {(["discrepancies", "all", "starred"] as Mode[]).map((m) => {
              const active = mode === m;
              return (
                <button
                  key={m}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setMode(m)}
                  className={cn(
                    "rounded-full px-4 py-2 font-heading text-xs font-bold capitalize transition-colors",
                    active
                      ? "bg-white text-[#1d1d1d] shadow-[0_1px_4px_rgba(29,29,29,0.12)] dark:bg-[#2fdebf] dark:text-[#1d1d1d]"
                      : "text-[#4a5058] hover:text-[#1d1d1d] dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]",
                  )}
                >
                  {MODE_LABELS[m]}
                </button>
              );
            })}
          </div>
          <div className="min-w-48 flex-1">
            <SingleSelectFilter
              options={questionOptions}
              value={question}
              onChange={setQuestion}
              placeholder="All questions"
              ariaLabel="Filter by question"
              filterPlaceholder="Search questions…"
              emptyText="No matches."
            />
          </div>
          <span className="font-heading text-xs font-bold text-[#4a5058] dark:text-[#C3C2B7]">
            {rows.length} rows · rated {rated}/{totalCells} cells ·{" "}
            {starsLoading ? "★ …" : `★ ${stars.length}`}
          </span>
        </div>
      </Card>

      {logsQuery.isLoading ? (
        <Card>
          <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
        </Card>
      ) : logsQuery.isError ? (
        <Card>
          <p role="alert" className="font-heading text-sm text-[#b91c1c] dark:text-[#f87171]">
            {serverDetail(logsQuery.error)}
          </p>
          <div className="mt-3">
            <Button
              variant="secondary"
              size="sm"
              loading={logsQuery.isFetching}
              onClick={() => void logsQuery.refetch()}
            >
              Retry
            </Button>
          </div>
        </Card>
      ) : selectedGroups.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            No Jev-vs-Opus pairs in this batch — pick another batch above.
          </p>
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            {mode === "discrepancies"
              ? "No disagreements — Jev and Opus agree on every question here."
              : mode === "starred"
                ? "No starred rows match these filters — tap ★ on a row to bookmark it."
                : "No rows match these filters."}
          </p>
        </Card>
      ) : narrow ? (
        <div className="grid gap-3">
          {rows.map((row) => {
            const starred = isStarred(stars, row.key);
            return (
            <Card
              key={row.key}
              className={cn(
                starred &&
                  "border-amber-300 bg-amber-50/60 dark:border-amber-400/40 dark:bg-amber-400/5",
              )}
            >
              <div className="flex min-w-0 items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]" title={row.sample}>
                    {row.sample}
                  </p>
                  <p className="mt-0.5 font-mono text-[10px] font-bold uppercase tracking-wide text-[#8a8f98]">
                    {row.questionKey}
                  </p>
                  {row.question !== "" ? (
                    <p className="mt-0.5 break-words font-sans text-xs text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]">
                      {row.question}
                    </p>
                  ) : (
                    <Dash />
                  )}
                </div>
                <span className="flex shrink-0 items-center gap-1.5">
                  <Badge tone={row.agree ? "success" : "warning"}>
                    {row.agree ? "agree" : "split"}
                  </Badge>
                  <FirefliesLink url={row.firefliesUrl} />
                </span>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div>
                  <p className="mb-1 font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:text-[#C3C2B7]">
                    Jev
                  </p>
                  <div className="flex items-start gap-1.5">
                    <StarButton starred={starred} onToggle={() => toggleStarKey(row.key)} />
                    <ModelCell
                      side={row.jev}
                      attr={row.questionKey}
                      showProb
                      feedback={feedback}
                      remarksFor={remarksFor}
                      onRemarksToggle={(k) => setRemarksFor((cur) => (cur === k ? null : k))}
                      onRemarksClose={() => setRemarksFor(null)}
                      onRemarksSave={saveRemarksForCell}
                    />
                  </div>
                </div>
                <div>
                  <p className="mb-1 font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:text-[#C3C2B7]">
                    Opus
                  </p>
                  <ModelCell
                    side={row.opus}
                    attr={row.questionKey}
                    showProb={false}
                    feedback={feedback}
                    remarksFor={remarksFor}
                    onRemarksToggle={(k) => setRemarksFor((cur) => (cur === k ? null : k))}
                    onRemarksClose={() => setRemarksFor(null)}
                    onRemarksSave={saveRemarksForCell}
                  />
                </div>
              </div>
              <div className="mt-3 flex min-w-0 gap-4 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                <span>
                  Ev# Jev <EvNum n={row.jev.evidence} />
                </span>
                <span>
                  Ev# Opus <EvNum n={row.opus.evidence} />
                </span>
              </div>
              <div className="mt-1.5 min-w-0">
                <EvidenceCell row={row} />
              </div>
            </Card>
            );
          })}
        </div>
      ) : (
        <Card padded={false} className="overflow-x-auto">
          <table className="w-full min-w-[1080px] text-left text-sm">
            <thead>
              <tr className="border-b border-[#e5e7eb] font-heading text-xs font-bold uppercase tracking-wide text-[#8a8f98] dark:border-white/10">
                <th className="px-4 py-3">Sample</th>
                <th className="px-4 py-3">Question</th>
                <th className="px-4 py-3">Jev</th>
                <th className="px-4 py-3">Opus</th>
                <th className="px-4 py-3">Ev# Jev</th>
                <th className="px-4 py-3">Ev# Opus</th>
                <th className="px-4 py-3">Evidence</th>
                <th className="px-4 py-3">Fireflies</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const starred = isStarred(stars, row.key);
                return (
                <tr
                  key={row.key}
                  className={cn(
                    "group/row border-b border-[#e5e7eb] align-top last:border-0 dark:border-white/10",
                    starred && "bg-amber-50/70 dark:bg-amber-400/5",
                  )}
                >
                  <td
                    className="max-w-48 truncate px-4 py-3 font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                    title={row.sample}
                  >
                    {row.sample}
                  </td>
                  <td className="min-w-64 max-w-96 px-4 py-3">
                    <div className="grid min-w-0 gap-1">
                      <p className="font-mono text-[10px] font-bold uppercase tracking-wide text-[#8a8f98]">
                        {row.questionKey}
                        {!row.agree && (
                          <span className="ml-1.5 inline-flex normal-case">
                            <Badge tone="warning">split</Badge>
                          </span>
                        )}
                      </p>
                      {row.question !== "" ? (
                        <p className="min-w-0 break-words font-sans text-xs text-[#4a5058] [overflow-wrap:anywhere] dark:text-[#C3C2B7]">
                          {row.question}
                        </p>
                      ) : (
                        <Dash />
                      )}
                    </div>
                  </td>
                  <td className="min-w-36 px-4 py-3">
                    <div className="flex items-start gap-1.5">
                      <StarButton starred={starred} onToggle={() => toggleStarKey(row.key)} />
                      <ModelCell
                        side={row.jev}
                        attr={row.questionKey}
                        showProb
                        feedback={feedback}
                        remarksFor={remarksFor}
                        onRemarksToggle={(k) => setRemarksFor((cur) => (cur === k ? null : k))}
                        onRemarksClose={() => setRemarksFor(null)}
                        onRemarksSave={saveRemarksForCell}
                      />
                    </div>
                  </td>
                  <td className="min-w-36 px-4 py-3">
                    <ModelCell
                      side={row.opus}
                      attr={row.questionKey}
                      showProb={false}
                      feedback={feedback}
                      remarksFor={remarksFor}
                      onRemarksToggle={(k) => setRemarksFor((cur) => (cur === k ? null : k))}
                      onRemarksClose={() => setRemarksFor(null)}
                      onRemarksSave={saveRemarksForCell}
                    />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <EvNum n={row.jev.evidence} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <EvNum n={row.opus.evidence} />
                  </td>
                  <td className="min-w-64 max-w-96 px-4 py-3">
                    <EvidenceCell row={row} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3">
                    <FirefliesLink url={row.firefliesUrl} />
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
