import { formatValue } from "../../lib/format";
import { cn } from "../../lib/cn";

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/**
 * Compact table for unwrapped v2 list attributes (raw JSON array, no wrapper).
 * One row per item, columns = union of item keys (first-seen order, with
 * confidence / confidence_type / evidence pinned last). Evidence renders as
 * small muted text with a tooltip. Old/wrapped values never reach here.
 */
export function UnwrappedListTable({ value }: { value: unknown }): React.JSX.Element {
  if (!Array.isArray(value) || value.length === 0) {
    return <span className="italic text-[#8a8f98]">— not found</span>;
  }
  const objs = value.filter(isPlainObject);
  if (objs.length !== value.length) {
    // Mixed/primitive list: fall back to pill rendering.
    return (
      <span className="flex min-w-0 max-w-full flex-wrap gap-1 break-words [overflow-wrap:anywhere]">
        {value.map((v, i) => (
          <span
            // eslint-disable-next-line react/no-array-index-key
            key={i}
            title={formatValue(v)}
            className="max-w-full break-words rounded-full border border-[#e5e7eb] bg-[#f1f2f3] px-2 py-0.5 font-sans text-[11px] text-[#1d1d1d] [overflow-wrap:anywhere] dark:border-white/10 dark:bg-white/10 dark:text-[#F0EFEC]"
          >
            {formatValue(v)}
          </span>
        ))}
      </span>
    );
  }
  const cols: string[] = [];
  const seen = new Set<string>();
  for (const o of objs) {
    for (const k of Object.keys(o)) {
      if (!seen.has(k)) {
        seen.add(k);
        cols.push(k);
      }
    }
  }
  // Pin per-item contract fields last for stable columns.
  const pinned = ["confidence", "confidence_type", "evidence"];
  cols.sort((a, b) => {
    const ai = pinned.indexOf(a);
    const bi = pinned.indexOf(b);
    if (ai === -1 && bi === -1) return 0;
    if (ai === -1) return -1;
    if (bi === -1) return 1;
    return ai - bi;
  });

  return (
    <span className="block min-w-0 max-w-full overflow-x-auto">
      <table className="w-full border-collapse font-sans text-[11px]">
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                scope="col"
                className="break-words border-b border-[#e5e7eb] px-1.5 py-1 text-left font-heading text-[10px] font-bold uppercase tracking-wide text-[#8a8f98] [overflow-wrap:anywhere] dark:border-white/10"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {objs.map((o, i) => (
            // eslint-disable-next-line react/no-array-index-key
            <tr key={i} className="border-b border-[#e5e7eb]/60 last:border-0 dark:border-white/5">
              {cols.map((c) => {
                const v = (o as Record<string, unknown>)[c];
                if (c === "evidence" && typeof v === "string") {
                  if (v.trim() === "") {
                    return (
                      <td key={c} className="break-words px-1.5 py-1 [overflow-wrap:anywhere]">
                        <span className="italic text-[#8a8f98]">—</span>
                      </td>
                    );
                  }
                  return (
                    <td key={c} className="break-words px-1.5 py-1 [overflow-wrap:anywhere]">
                      <span
                        className="block max-w-40 truncate italic text-[#4a5058] dark:text-[#C3C2B7]"
                        title={v}
                      >
                        “{v}”
                      </span>
                    </td>
                  );
                }
                if (c === "confidence" && typeof v === "number") {
                  return (
                    <td key={c} className="break-words px-1.5 py-1 font-mono [overflow-wrap:anywhere]">
                      {Number.isFinite(v) ? v.toFixed(2) : "?"}
                    </td>
                  );
                }
                const text = formatValue(v);
                return (
                  <td
                    key={c}
                    className={cn(
                      "break-words px-1.5 py-1 text-[#1d1d1d] [overflow-wrap:anywhere] dark:text-[#F0EFEC]",
                      (c === "confidence_type" || c === "confidence") && "font-mono",
                    )}
                    title={text}
                  >
                    {text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </span>
  );
}
