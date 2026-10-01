import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { Badge } from "../components/ui/Badge";
import { Card } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";

type LogRow = {
  id: string;
  run_id: string;
  input_type: string;
  input_data: string;
  model: string;
  agent_snapshot: Record<string, { name: string; attributes: { name: string; type: string }[] }>;
  outputs: Record<string, unknown>;
  feedback: Record<string, Record<string, { rating: string; remarks: string }>>;
  created_at: string;
};

function fmt(ts: string) {
  try {
    return new Date(ts).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" });
  } catch {
    return ts;
  }
}

const sectionLabel = "font-heading text-xs font-bold uppercase tracking-wide text-[#4a5058] dark:text-[#C3C2B7]";
const codeBlock =
  "mt-1.5 max-h-60 overflow-auto rounded-xl bg-[#f1f2f3] p-3 font-mono text-xs text-[#1d1d1d] dark:bg-white/5 dark:text-[#F0EFEC]";

export default function Logs() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["logs"],
    queryFn: async () => (await api.get("/logs")).data as LogRow[],
  });
  const [open, setOpen] = useState<string | null>(null);

  return (
    <div className="grid gap-4">
      <PageHeader
        title="Run logs"
        description="Frozen snapshots per run — agent configs, attributes, model, outputs, feedback. Never linked to live config."
      />

      {isLoading ? (
        <Card>
          <p className="font-heading text-sm text-[#8a8f98]">Loading…</p>
        </Card>
      ) : data.length === 0 ? (
        <Card>
          <p className="font-heading text-sm text-[#4a5058] dark:text-[#C3C2B7]">
            No runs logged yet — run something from the Test Lab.
          </p>
        </Card>
      ) : (
        <div className="grid gap-3">
          {data.map((l) => {
            const expanded = open === l.id;
            const fbCount = Object.values(l.feedback ?? {}).reduce((n, m) => n + Object.keys(m).length, 0);
            return (
              <Card key={l.id} padded={false} className="p-4">
                <button onClick={() => setOpen(expanded ? null : l.id)} className="flex w-full flex-wrap items-center gap-2 text-left">
                  <Badge tone="brand">{l.input_type}</Badge>
                  <span className="truncate font-mono text-xs text-[#4a5058] dark:text-[#C3C2B7]">{l.model}</span>
                  <span className="font-heading text-xs text-[#8a8f98]">{fmt(l.created_at)}</span>
                  <span className="ml-auto flex items-center gap-2 font-heading text-xs text-[#4a5058] dark:text-[#C3C2B7]">
                    {Object.keys(l.agent_snapshot ?? {}).length} agents · {fbCount} ratings
                    <span aria-hidden="true" className={cn("transition-transform", expanded && "rotate-180")}>▾</span>
                  </span>
                </button>
                {expanded && (
                  <div className="mt-3 grid gap-4 border-t border-[#e5e7eb] pt-3 dark:border-white/10">
                    <div>
                      <h3 className={sectionLabel}>Input</h3>
                      <pre className={cn(codeBlock, "max-h-40 whitespace-pre-wrap font-sans")}>{l.input_data}</pre>
                    </div>
                    <div>
                      <h3 className={sectionLabel}>Agent snapshots (frozen at run time)</h3>
                      <pre className={codeBlock}>{JSON.stringify(l.agent_snapshot, null, 2)}</pre>
                    </div>
                    <div>
                      <h3 className={sectionLabel}>Outputs</h3>
                      <pre className={codeBlock}>{JSON.stringify(l.outputs, null, 2)}</pre>
                    </div>
                    <div>
                      <h3 className={sectionLabel}>Feedback</h3>
                      {fbCount === 0 ? (
                        <p className="mt-1.5 font-heading text-xs text-[#8a8f98]">No ratings yet.</p>
                      ) : (
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {Object.entries(l.feedback).map(([agent, attrs]) =>
                            Object.entries(attrs).map(([attr, f]) => (
                              <span
                                key={`${agent}-${attr}`}
                                title={f.remarks || undefined}
                                className="inline-flex items-center gap-1.5 rounded-full bg-[#f1f2f3] px-2.5 py-1 font-heading text-[11px] font-bold text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                              >
                                {agent} / {attr} {f.rating === "up" ? "👍" : "👎"}
                              </span>
                            )),
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
