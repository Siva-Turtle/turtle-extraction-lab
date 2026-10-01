import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export default function Agents() {
  const { data } = useQuery({ queryKey: ["agents"], queryFn: async () => (await api.get("/agents")).data });
  return (
    <section className="rounded-xl border border-ink-100 bg-surface p-5 shadow-float">
      <h1 className="font-module text-lg">Agents</h1>
      <p className="mt-1 font-heading text-xs text-ink-400">Define system instruction + prompt per agent.</p>
      <pre className="mt-4 overflow-auto rounded-lg bg-ink-50 p-3 text-xs">{JSON.stringify(data ?? [], null, 2)}</pre>
    </section>
  );
}
