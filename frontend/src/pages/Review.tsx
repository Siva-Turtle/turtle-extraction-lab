import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Scale } from "lucide-react";
import { api } from "../lib/api";
import { fmt, fmtCostBoth, modelLabel, serverDetail } from "../lib/format";
import { IDENTIFIER_QUESTION_KEYS } from "../lib/format";
import type { LogRow } from "../lib/logTypes";
import { buildRows, chunkText, pairGroups } from "../lib/review";
import type { ReviewGroup, ReviewRow, ReviewSide } from "../lib/review";
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

type Mode = "discrepancies" | "all";

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

function GroupPicker({
  groups,
  selected,
  onToggle,
  onAll,
  onNone,
}: {
  groups: ReviewGroup[];
  selected: string[];
  onToggle: (id: string) => void;
  onAll: () => void;
  onNone: () => void;
}): React.JSX.Element {
  const set = new Set(selected);
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-heading text-sm font-bold text-[#1d1d1d] dark:text-[#F0EFEC]">
          Pair groups ({selected.length}/{groups.length} selected)
        </h2>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={onAll}>
            All
          </Button>
          <Button variant="secondary" size="sm" onClick={onNone}>
            None
          </Button>
        </div>
      </div>
      {groups.length === 0 ? (
        <p className="mt-2 font-sans text-sm text-[#4a5058] dark:text-[#C3C2B7]">
          No Jev-vs-Opus pairs in the fetched logs — run a group with one{" "}
          <span className="font-mono text-xs">typesafe/jev-*</span> and one{" "}
          <span className="font-mono text-xs">anthropic/*</span> model first.
        </p>
      ) : (
        <ul className="mt-3 grid gap-2">
          {groups.map((g) => {
            const checked = set.has(g.id);
            return (
              <li key={g.id}>
                <label className="flex min-w-0 cursor-pointer items-start gap-2.5 rounded-xl border border-[#e5e7eb] p-2.5 hover:border-[#1d1d1d] dark:border-white/10">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => onToggle(g.id)}
                    aria-label={`Select ${g.title}`}
                    className="mt-1 h-4 w-4 shrink-0 accent-[#0d5c4a]"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]" title={g.title}>
                      {g.title}
                    </span>
                    <span className="mt-0.5 block font-sans text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                      {fmt(g.createdAt)}
                    </span>
                    <span className="mt-1 flex flex-wrap items-center gap-1">
                      <Badge tone="neutral">
                        <span className="max-w-40 truncate font-mono normal-case" title={g.jevModel}>
                          {modelLabel(g.jevModel, "", "")}
                        </span>
                      </Badge>
                      <Badge tone="neutral">
                        <span className="max-w-40 truncate font-mono normal-case" title={g.opusModel}>
                          {modelLabel(g.opusModel, "", "")}
                        </span>
                      </Badge>
                      <span className="font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                        {fmtCostBoth(g.cost)}
                      </span>
                    </span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

export default function Review(): React.JSX.Element {
  const narrow = useIsNarrow();
  const feedback = useFeedback();
  const [mode, setMode] = React.useState<Mode>("discrepancies");
  const [question, setQuestion] = React.useState("all");
  const [selected, setSelected] = React.useState<string[] | null>(null);
  const [remarksFor, setRemarksFor] = React.useState<string | null>(null);

  const logsQuery = useQuery({
    queryKey: ["logs-all"],
    queryFn: async () => (await api.get("/logs")).data as LogRow[],
    staleTime: 30 * 1000,
  });
  const logs = logsQuery.data ?? [];
  const groups = React.useMemo(() => pairGroups(logs), [logs]);

  // Default = all pair groups from the fetched set.
  const effectiveSelected = selected ?? groups.map((g) => g.id);
  const selectedSet = React.useMemo(() => new Set(effectiveSelected), [effectiveSelected]);
  const selectedGroups = React.useMemo(
    () => groups.filter((g) => selectedSet.has(g.id)),
    [groups, selectedSet],
  );

  const allRows = React.useMemo(() => buildRows(selectedGroups), [selectedGroups]);
  const rows = React.useMemo(() => {
    let out = allRows;
    if (mode === "discrepancies") out = out.filter((r) => !r.agree);
    if (question !== "all") out = out.filter((r) => r.questionKey === question);
    return out;
  }, [allRows, mode, question]);

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

  function toggleGroup(id: string): void {
    const cur = new Set(effectiveSelected);
    if (cur.has(id)) cur.delete(id);
    else cur.add(id);
    setSelected([...cur]);
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
          <span className="inline-flex items-center gap-1.5 rounded-full bg-[#f1f2f3] px-2.5 py-1 font-heading text-[11px] font-bold uppercase tracking-wide text-[#4a5058] dark:bg-white/10 dark:text-[#C3C2B7]">
            <Scale className="h-3.5 w-3.5" aria-hidden="true" />
            Jev vs Opus · rated {rated}/{totalCells}
          </span>
        }
      />

      <GroupPicker
        groups={groups}
        selected={effectiveSelected}
        onToggle={toggleGroup}
        onAll={() => setSelected(groups.map((g) => g.id))}
        onNone={() => setSelected([])}
      />

      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Row filter"
            className="flex items-center gap-1 rounded-full border border-[#e5e7eb] bg-[#f1f2f3] p-1 dark:border-white/10 dark:bg-white/5"
          >
            {(["discrepancies", "all"] as Mode[]).map((m) => {
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
                  {m === "discrepancies" ? "Discrepancies" : "All"}
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
            {rows.length} rows · rated {rated}/{totalCells} cells
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
            Select at least one pair group above to review.
          </p>
        </Card>
      ) : rows.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            {mode === "discrepancies"
              ? "No disagreements — Jev and Opus agree on every question here."
              : "No rows match these filters."}
          </p>
        </Card>
      ) : narrow ? (
        <div className="grid gap-3">
          {rows.map((row) => (
            <Card key={row.key}>
              <div className="flex min-w-0 items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]" title={row.sample}>
                    {row.sample}
                  </p>
                  <p className="mt-0.5 font-mono text-[11px] text-[#4a5058] dark:text-[#C3C2B7]">
                    {row.questionKey}
                  </p>
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
          ))}
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
              {rows.map((row) => (
                <tr
                  key={row.key}
                  className="group/row border-b border-[#e5e7eb] align-top last:border-0 dark:border-white/10"
                >
                  <td
                    className="max-w-48 truncate px-4 py-3 font-heading text-xs font-bold text-[#1d1d1d] dark:text-[#F0EFEC]"
                    title={row.sample}
                  >
                    {row.sample}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                    {row.questionKey}
                    {!row.agree && (
                      <span className="ml-1.5 inline-flex">
                        <Badge tone="warning">split</Badge>
                      </span>
                    )}
                  </td>
                  <td className="min-w-36 px-4 py-3">
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
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
