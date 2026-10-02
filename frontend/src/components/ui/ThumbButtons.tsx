import * as React from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * Paired thumbs up/down buttons (extracted from Logs.tsx / TestLab.tsx).
 * Clicking the active thumb calls `onChange(null)`; call sites that do not
 * support un-rating map `null` back so behaviour is unchanged.
 */
export function ThumbButtons({
  value,
  onChange,
  size = "md",
  disabled,
}: {
  value: "up" | "down" | null;
  onChange: (next: "up" | "down" | null) => void;
  size?: "sm" | "md";
  disabled?: boolean;
}): React.JSX.Element {
  const dims = size === "sm" ? "h-7 w-7" : "h-8 w-8";
  const icon = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";

  function handle(which: "up" | "down") {
    onChange(value === which ? null : which);
  }

  return (
    <>
      <button
        type="button"
        onClick={() => handle("up")}
        title="Thumbs up"
        aria-pressed={value === "up"}
        aria-label="Thumbs up"
        disabled={disabled}
        className={cn(
          "flex items-center justify-center rounded-full border transition-colors",
          dims,
          value === "up"
            ? "border-[#22c55e] bg-[#e9f9ef] text-[#15803d]"
            : "border-[#e5e7eb] text-[#4a5058] hover:border-[#22c55e] dark:border-white/10 dark:text-[#C3C2B7]",
        )}
      >
        <ThumbsUp className={icon} aria-hidden="true" />
      </button>
      <button
        type="button"
        onClick={() => handle("down")}
        title="Thumbs down"
        aria-pressed={value === "down"}
        aria-label="Thumbs down"
        disabled={disabled}
        className={cn(
          "flex items-center justify-center rounded-full border transition-colors",
          dims,
          value === "down"
            ? "border-[#ef4444] bg-[#fdecec] text-[#b91c1c]"
            : "border-[#e5e7eb] text-[#4a5058] hover:border-[#ef4444] dark:border-white/10 dark:text-[#C3C2B7]",
        )}
      >
        <ThumbsDown className={icon} aria-hidden="true" />
      </button>
    </>
  );
}
