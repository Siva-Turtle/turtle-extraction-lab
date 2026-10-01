import * as React from "react";
import { cn } from "../../lib/cn";
import { CardTitle } from "./Card";
import { Button } from "./Button";

export const fieldLabel =
  "font-heading text-xs font-bold uppercase tracking-wide text-[#4a5058] dark:text-[#C3C2B7]";

export const fieldInput =
  "mt-1.5 h-11 w-full min-w-0 rounded-xl border bg-white px-3 font-sans text-[#1d1d1d] placeholder:text-[#8a8f98] " +
  "dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC] dark:placeholder:text-[#898781] " +
  "focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1 dark:focus-visible:outline-[#2fdebf] " +
  "border-[#e5e7eb] hover:border-[#1d1d1d] focus:border-transparent dark:hover:border-white/40";

export const fieldTextarea =
  "mt-1.5 w-full min-w-0 rounded-xl border bg-white px-3 py-2.5 font-sans text-sm text-[#1d1d1d] placeholder:text-[#8a8f98] " +
  "dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC] dark:placeholder:text-[#898781] " +
  "focus-visible:outline-2 focus-visible:outline-[#1d1d1d] focus-visible:outline-offset-1 dark:focus-visible:outline-[#2fdebf] " +
  "border-[#e5e7eb] hover:border-[#1d1d1d] focus:border-transparent dark:hover:border-white/40";

/** Bottom-sheet on mobile, centred dialog on md+ — same pattern as the CRM shell. */
export function Modal({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}): React.JSX.Element {
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 animate-[turtle-fade-in_150ms_ease-out] sm:items-center sm:p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          "max-h-[90vh] w-full overflow-auto rounded-t-2xl border border-[#e5e7eb] bg-white p-5 animate-[turtle-sheet-in-bottom_180ms_ease-out] sm:rounded-2xl dark:border-white/10 dark:bg-[#1a1a1a]",
          wide ? "max-w-3xl" : "max-w-2xl",
        )}
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <CardTitle>{title}</CardTitle>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="Close dialog">
            ✕
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}
