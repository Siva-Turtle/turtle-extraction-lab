import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, Star } from "lucide-react";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { fieldInput } from "./Modal";

export type ModelOption = { id: string; name: string };

const FAV_MODELS_KEY = "lab:favourite-models";

function loadFavouriteModels(): string[] {
  try {
    const raw = localStorage.getItem(FAV_MODELS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((v): v is string => typeof v === "string");
  } catch {
    return [];
  }
}

/**
 * Searchable model picker: type to filter, arrows + Enter to pick,
 * free text always allowed (custom model ids). Mirrors the v2 field style.
 */
export function ModelCombobox({
  value,
  onChange,
  onLiveChange,
}: {
  value: string;
  onChange: (v: string) => void;
  onLiveChange?: (live: boolean) => void;
}): React.JSX.Element {
  const { data } = useQuery({
    queryKey: ["models"],
    queryFn: async () => (await api.get("/models")).data as { live: boolean; models: ModelOption[] },
    staleTime: 5 * 60 * 1000,
  });
  const options = data?.models ?? [];
  React.useEffect(() => {
    onLiveChange?.(data?.live ?? false);
  }, [data?.live, onLiveChange]);

  const [open, setOpen] = React.useState(false);
  const [highlight, setHighlight] = React.useState(0);
  const rootRef = React.useRef<HTMLDivElement>(null);
  const [favourites, setFavourites] = React.useState<string[]>(loadFavouriteModels);
  const favSet = React.useMemo(() => new Set(favourites), [favourites]);

  React.useEffect(() => {
    try {
      localStorage.setItem(FAV_MODELS_KEY, JSON.stringify(favourites));
    } catch {
      // ignore persistence failures (private mode, quota)
    }
  }, [favourites]);

  function toggleFavourite(id: string) {
    setFavourites((prev) => (prev.includes(id) ? prev.filter((f) => f !== id) : [...prev, id]));
  }

  const q = value.trim().toLowerCase();
  const filtered = q
    ? options.filter((o) => o.id.toLowerCase().includes(q) || o.name.toLowerCase().includes(q))
    : options;
  const rows = [...filtered]
    .sort((a, b) => {
      const af = favSet.has(a.id) ? 0 : 1;
      const bf = favSet.has(b.id) ? 0 : 1;
      if (af !== bf) return af - bf;
      return a.id.localeCompare(b.id);
    })
    .slice(0, 50);
  const exact = options.some((o) => o.id === value.trim());
  const showCustom = value.trim() !== "" && !exact;

  React.useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  React.useEffect(() => setHighlight(0), [value]);

  function pick(v: string) {
    onChange(v);
    setOpen(false);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const total = rows.length + (showCustom ? 1 : 0);
      setHighlight((h) => (total === 0 ? 0 : Math.min(h + 1, total - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      if (highlight < rows.length) pick(rows[highlight].id);
      else if (showCustom) pick(value.trim());
    }
  }

  const customIndex = rows.length; // "use custom" row sits after options

  return (
    <div ref={rootRef} className="relative mt-1.5">
      <input
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        placeholder="e.g. anthropic/claude-sonnet-4 — type to search"
        className="h-11 w-full min-w-0 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-10 font-sans text-[#1d1d1d] placeholder:text-[#8a8f98] hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1 dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC] dark:placeholder:text-[#898781] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]"
      />
      <button
        type="button"
        tabIndex={-1}
        aria-label="Toggle model list"
        onClick={() => setOpen((o) => !o)}
        className="absolute right-1 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10"
      >
        <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>
      {open && (
        <ul
          role="listbox"
          className="absolute inset-x-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-2xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] animate-[turtle-fade-in_120ms_ease-out] dark:border-white/10 dark:bg-[#1a1a1a]"
        >
          {rows.map((o, i) => {
            const isFav = favSet.has(o.id);
            return (
              <li key={o.id} role="option" aria-selected={value.trim() === o.id}>
                <div
                  onMouseEnter={() => setHighlight(i)}
                  className={cn(
                    "flex w-full items-center gap-1 rounded-lg py-1 pl-1 pr-1",
                    i === highlight
                      ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                      : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                  )}
                >
                  <button
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pick(o.id)}
                    aria-label={`Select model ${o.id}`}
                    className="flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1 text-left font-sans text-sm"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-xs">{o.id}</span>
                      {o.name !== o.id && (
                        <span className="block truncate text-xs text-[#4a5058] dark:text-[#C3C2B7]">{o.name}</span>
                      )}
                    </span>
                    {value.trim() === o.id && (
                      <Check className="h-4 w-4 shrink-0 text-[#0d5c4a] dark:text-[#2fdebf]" aria-hidden="true" />
                    )}
                  </button>
                  <button
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleFavourite(o.id);
                    }}
                    aria-label={isFav ? `Remove ${o.id} from favourites` : `Add ${o.id} to favourites`}
                    aria-pressed={isFav}
                    title={isFav ? "Remove from favourites" : "Add to favourites"}
                    className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-white hover:text-[#0d5c4a] dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#2fdebf]"
                  >
                    <Star
                      className="h-4 w-4"
                      fill={isFav ? "currentColor" : "none"}
                      aria-hidden="true"
                    />
                  </button>
                </div>
              </li>
            );
          })}
          {showCustom && (
            <li role="option" aria-selected={false}>
              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(value.trim())}
                onMouseEnter={() => setHighlight(customIndex)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left font-sans text-sm text-[#1d1d1d] dark:text-[#F0EFEC]",
                  highlight === customIndex && "bg-[#e8fbf6] dark:bg-white/10",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-xs">Use “{value.trim()}”</span>
                  <span className="block text-xs text-[#4a5058] dark:text-[#C3C2B7]">custom model id</span>
                </span>
              </button>
            </li>
          )}
          {rows.length === 0 && !showCustom && (
            <li className="px-3 py-2 font-sans text-sm text-[#8a8f98]">No matches — keep typing for a custom id.</li>
          )}
        </ul>
      )}
    </div>
  );
}

export type MultiSelectOption = { value: string; label: string; sub?: string };

const multiTrigger =
  "flex h-11 w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-[#e5e7eb] bg-white py-2 pl-3 pr-2 font-sans text-sm " +
  "hover:border-[#1d1d1d] focus:border-transparent focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1 " +
  "dark:border-white/10 dark:bg-[#2e2e2e] dark:hover:border-white/40 dark:focus-visible:outline-[#2fdebf]";

const multiList =
  "absolute inset-x-0 top-full z-20 mt-1 max-h-64 overflow-auto rounded-2xl border border-[#e5e7eb] bg-white p-1.5 shadow-[0_8px_24px_rgba(29,29,29,0.08)] animate-[turtle-fade-in_120ms_ease-out] dark:border-white/10 dark:bg-[#1a1a1a]";

/**
 * Searchable multi-select filter — v2 field + dropdown styling shared with the
 * Agents/TestLab multiselects. Collapsed shows `first + N others`, never pills.
 */
export function MultiSelectFilter({
  options,
  selected,
  onChange,
  placeholder,
  ariaLabel,
  filterPlaceholder = "Filter…",
  emptyText = "No matches.",
}: {
  options: MultiSelectOption[];
  selected: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
  ariaLabel: string;
  filterPlaceholder?: string;
  emptyText?: string;
}): React.JSX.Element {
  const [open, setOpen] = React.useState(false);
  const [filter, setFilter] = React.useState("");

  const labelOf = React.useCallback(
    (v: string) => options.find((o) => o.value === v)?.label ?? v,
    [options],
  );
  const orderedLabels = selected.map(labelOf).filter((n) => n !== "");
  const q = filter.trim().toLowerCase();
  const visible = q
    ? options.filter(
        (o) =>
          o.label.toLowerCase().includes(q) ||
          o.value.toLowerCase().includes(q) ||
          (o.sub?.toLowerCase().includes(q) ?? false),
      )
    : options;

  function toggle(v: string) {
    onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  }

  const label =
    orderedLabels.length === 0
      ? placeholder
      : orderedLabels.length === 1
        ? orderedLabels[0]
        : `${orderedLabels[0]} + ${orderedLabels.length - 1} other${orderedLabels.length - 1 === 1 ? "" : "s"}`;

  return (
    <div className="relative mt-1.5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className={multiTrigger}
      >
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-left",
            orderedLabels.length > 0 ? "text-[#1d1d1d] dark:text-[#F0EFEC]" : "text-[#8a8f98] dark:text-[#898781]",
          )}
        >
          {label}
        </span>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] dark:text-[#C3C2B7] dark:hover:bg-white/10">
          <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
        </span>
      </button>
      {open && (
        <>
          <button
            type="button"
            aria-hidden="true"
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-10 cursor-default bg-transparent"
          />
          <div role="listbox" aria-label={ariaLabel} className={multiList}>
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={filterPlaceholder}
              aria-label={`Filter ${ariaLabel}`}
              className={fieldInput}
            />
            <div className="mt-2 grid gap-1">
              {visible.length === 0 ? (
                <p className="px-2 py-1 font-sans text-xs text-[#8a8f98]">{emptyText}</p>
              ) : (
                visible.map((o) => (
                  <label
                    key={o.value}
                    className={cn(
                      "flex cursor-pointer items-center gap-2 rounded-lg px-3 py-2 font-sans text-sm",
                      selected.includes(o.value)
                        ? "bg-[#e8fbf6] text-[#1d1d1d] dark:bg-white/10 dark:text-[#F0EFEC]"
                        : "text-[#1d1d1d] dark:text-[#F0EFEC]",
                    )}
                  >
                    <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">{o.label}</span>
                      {o.sub && (
                        <span className="block truncate text-xs text-[#4a5058] dark:text-[#C3C2B7]">{o.sub}</span>
                      )}
                    </span>
                    {selected.includes(o.value) && (
                      <Check className="h-4 w-4 shrink-0 text-[#0d5c4a] dark:text-[#2fdebf]" aria-hidden="true" />
                    )}
                  </label>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
