import * as React from "react";
import { cn } from "../../lib/cn";

export type BadgeTone = "default" | "success" | "warning" | "danger" | "info" | "brand" | "neutral";

const toneClasses: Record<BadgeTone, string> = {
  brand: "bg-[#e8fbf6] text-[#0d5c4a] dark:bg-[#2fdebf]/15 dark:text-[#5ee8cf]",
  success: "bg-[#e9f9ef] text-[#15803d] dark:bg-[#22c55e]/15 dark:text-[#4ade80]",
  warning: "bg-[#fef6e7] text-[#b45309] dark:bg-[#f59e0b]/15 dark:text-[#fbbf24]",
  danger: "bg-[#fdecec] text-[#b91c1c] dark:bg-[#ef4444]/15 dark:text-[#f87171]",
  info: "bg-[#eaf1fe] text-[#1d4ed8] dark:bg-[#3b82f6]/15 dark:text-[#93c5fd]",
  neutral: "bg-[#f1f2f3] text-[#4a5058] dark:bg-white/10 dark:text-[#C3C2B7]",
  default: "bg-[#f1f2f3] text-[#4a5058] dark:bg-white/10 dark:text-[#C3C2B7]",
};

const dotClasses: Record<BadgeTone, string> = {
  brand: "bg-[#2fdebf]",
  success: "bg-[#22c55e]",
  warning: "bg-[#f59e0b]",
  danger: "bg-[#ef4444]",
  info: "bg-[#3b82f6]",
  neutral: "bg-[#8a8f98]",
  default: "bg-[#8a8f98]",
};

/** Turtle theme badge: tinted pill, glowing status dot, uppercase micro type. */
export function Badge({
  tone = "default",
  className,
  children,
}: {
  tone?: BadgeTone;
  className?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-heading text-[11px] font-bold uppercase tracking-wide",
        toneClasses[tone],
        className,
      )}
    >
      <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", dotClasses[tone])} />
      {children}
    </span>
  );
}
