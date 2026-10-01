import * as React from "react";
import { cn } from "../../lib/cn";

export interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  className?: string;
}

/** Turtle theme page header (mint accent bar, display title) — mirrors PageHeaderV2. */
export function PageHeader({ title, description, actions, className }: PageHeaderProps): React.JSX.Element {
  return (
    <div className={cn("flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between", className)}>
      <div className="min-w-0">
        <span aria-hidden="true" className="mb-2 block h-1 w-10 rounded-full bg-[#2fdebf]" />
        <h1 className="truncate font-module text-2xl font-bold tracking-tight text-[#1d1d1d] dark:text-[#F0EFEC] sm:text-[32px] sm:leading-tight">
          {title}
        </h1>
        {description ? (
          <p className="mt-1 max-w-2xl text-sm text-[#4a5058] dark:text-[#C3C2B7]">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
