import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

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

export default function Logs() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["logs"],
    queryFn: async () => (await api.get("/logs")).data as LogRow[],
  });
  const [open, setOpen] = useState<string | null>(null);

  return (
    <section className="rounded-xl border border-ink-100 bg-surface p-5 shadow-float">
      <h1 className="font-module text-lg">Run logs</h1>
      <p className="mt-1 font-heading text-xs text-ink-400">
        Frozen snapshots per run — agent configs, attributes, model, outputs, feedback. Never linked to live config.
      </p>

      {isLoading ? (
        <p className="mt-4 font-heading text-sm text-ink-400">Loading…</p>
      ) : data.length === 0 ? (
        <p className="mt-4 rounded-lg bg-ink-50 p-4 font-heading text-sm text-ink-500">
          No runs logged yet — run something from the Test Lab.
        </p>
      ) : (
        <div className="mt-4 grid gap-3">
          {data.map((l) => {
            const expanded = open === l.id;
            const fbCount = Object.values(l.feedback ?? {}).reduce((n, m) => n + Object.keys(m).length, 0);
            return (
              <div key={l.id} className="rounded-xl border border-ink-100 p-4">
                <button onClick={() => setOpen(expanded ? null : l.id)} className="flex w-full flex-wrap items-center gap-2 text-left">
                  <span className="font-heading text-sm font-semibold">{l.input_type}</span>
                  <span className="rounded-full bg-ink-50 px-2 py-0.5 font-heading text-[11px]">{l.model}</span>
                  <span className="font-heading text-xs text-ink-400">{fmt(l.created_at)}</span>
                  <span className="ml-auto font-heading text-xs text-ink-500">
                    {Object.keys(l.agent_snapshot ?? {}).length} agents · {fbCount} ratings · {expanded ? "▾" : "▸"}
                  </span>
                </button>
                {expanded && (
                  <div className="mt-3 grid gap-3 text-sm">
                    <div>
                      <h3 className="font-heading text-xs font-semibold text-ink-500">INPUT</h3>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-ink-50 p-3 text-xs">{l.input_data}</pre>
                    </div>
                    <div>
                      <h3 className="font-heading text-xs font-semibold text-ink-500">AGENT SNAPSHOTS (frozen at run time)</h3>
                      <pre className="mt-1 max-h-60 overflow-auto rounded-lg bg-ink-50 p-3 text-xs">
                        {JSON.stringify(l.agent_snapshot, null, 2)}
                      </pre>
                    </div>
                    <div>
                      <h3 className="font-heading text-xs font-semibold text-ink-500">OUTPUTS</h3>
                      <pre className="mt-1 max-h-60 overflow-auto rounded-lg bg-ink-50 p-3 text-xs">
                        {JSON.stringify(l.outputs, null, 2)}
                      </pre>
                    </div>
                    <div>
                      <h3 className="font-heading text-xs font-semibold text-ink-500">FEEDBACK</h3>
                      {fbCount === 0 ? (
                        <p className="mt-1 font-heading text-xs text-ink-400">No ratings yet.</p>
                      ) : (
                        <div className="mt-1 grid gap-1">
                          {Object.entries(l.feedback).map(([agent, attrs]) =>
                            Object.entries(attrs).map(([attr, f]) => (
                              <p key={`${agent}-${attr}`} className="text-xs">
                                <span className="font-heading font-semibold">{agent} / {attr}</span>{" "}
                                {f.rating === "up" ? "👍" : "👎"}
                                {f.remarks && <span className="text-ink-500"> — {f.remarks}</span>}
                              </p>
                            )),
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
