import * as React from "react";
import { X } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * Right-side slide-over drawer (v2 theme) — overlay click + X + Escape closes.
 * Body scroll is locked while open; the close button takes initial focus.
 */
export function Drawer({
  title,
  onClose,
  children,
}: {
  title: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}): React.JSX.Element {
  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  React.useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 animate-[turtle-fade-in_150ms_ease-out]"
      role="dialog"
      aria-modal="true"
    >
      <div
        aria-hidden="true"
        className="absolute inset-0 bg-black/40"
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      />
      <aside
        className={cn(
          "absolute right-0 top-0 flex h-full w-full max-w-2xl flex-col",
          "border-l border-[#e5e7eb] bg-white shadow-[0_8px_24px_rgba(29,29,29,0.08)]",
          "dark:border-white/10 dark:bg-[#1a1a1a]",
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b border-[#e5e7eb] p-4 dark:border-white/10">
          <div className="min-w-0 flex-1">{title}</div>
          <button
            autoFocus
            onClick={onClose}
            aria-label="Close log details"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[#4a5058] hover:bg-[#e8fbf6] hover:text-[#1d1d1d] dark:text-[#C3C2B7] dark:hover:bg-white/10 dark:hover:text-[#F0EFEC]"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">{children}</div>
      </aside>
    </div>
  );
}
