import * as React from "react";
import { Button } from "./Button";

/**
 * Small absolutely positioned remarks panel: text input, Save button,
 * Esc closes, Enter saves.
 */
export function RemarksPopover({
  value,
  onSave,
  onClose,
}: {
  value: string;
  onSave: (text: string) => void;
  onClose: () => void;
}): React.JSX.Element {
  const [text, setText] = React.useState(value);
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  function save() {
    onSave(text);
  }

  return (
    <div
      role="dialog"
      aria-label="Edit remarks"
      className="absolute bottom-full left-0 z-30 mb-1.5 w-60 rounded-xl border border-[#e5e7eb] bg-white p-2 shadow-[0_8px_24px_rgba(29,29,29,0.12)] dark:border-white/10 dark:bg-[#1a1a1a]"
      onMouseDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter" && (e.target as HTMLElement)?.tagName === "INPUT") {
          e.preventDefault();
          save();
        }
      }}
    >
      <input
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Remarks…"
        aria-label="Remarks"
        className="h-8 w-full min-w-0 rounded-lg border border-[#e5e7eb] bg-white px-2 font-sans text-xs text-[#1d1d1d] placeholder:text-[#8a8f98] hover:border-[#1d1d1d] dark:border-white/10 dark:bg-[#2e2e2e] dark:text-[#F0EFEC]"
      />
      <div className="mt-1.5 flex items-center justify-end gap-1.5">
        <button
          type="button"
          onClick={onClose}
          className="px-1.5 py-1 font-heading text-[11px] font-bold text-[#4a5058] hover:text-[#1d1d1d] focus-visible:outline-2 focus-visible:outline-brand dark:text-[#C3C2B7] dark:hover:text-[#F0EFEC]"
        >
          Cancel
        </button>
        <Button variant="secondary" size="sm" onClick={save}>
          Save
        </Button>
      </div>
    </div>
  );
}
