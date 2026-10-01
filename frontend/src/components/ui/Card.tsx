import * as React from "react";
import { cn } from "../../lib/cn";

/** Turtle theme paper card (white, line border, float shadow) — mirrors CardV2. */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  padded?: boolean;
}

export function Card({ padded = true, className, ...rest }: CardProps): React.JSX.Element {
  return (
    <div
      className={cn(
        "min-w-0 rounded-2xl border border-[#e5e7eb] bg-white",
        "shadow-[0_8px_24px_rgba(29,29,29,0.08)]",
        "dark:border-white/10 dark:bg-[#1a1a1a] dark:shadow-[0_8px_24px_rgba(0,0,0,0.5)]",
        padded && "p-4 sm:p-6",
        className,
      )}
      {...rest}
    />
  );
}

export function CardTitle({ className, ...rest }: React.HTMLAttributes<HTMLHeadingElement>): React.JSX.Element {
  return (
    <h3 className={cn("font-heading text-base font-bold text-[#1d1d1d] dark:text-[#F0EFEC]", className)} {...rest} />
  );
}
