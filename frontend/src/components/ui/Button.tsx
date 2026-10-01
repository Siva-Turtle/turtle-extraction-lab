import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "../../lib/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

/** Turtle theme buttons — mirrors ButtonV2 (ink-bar primary, pill radius). */
const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-[#1d1d1d] text-white border border-transparent hover:bg-[#383838] active:bg-black " +
    "dark:bg-[#2fdebf] dark:text-[#1d1d1d] dark:hover:bg-[#5ee8cf] dark:active:bg-[#2fdebf]",
  secondary:
    "bg-white text-[#1d1d1d] border border-[#e5e7eb] hover:border-[#1d1d1d] hover:bg-[#f1f2f3] " +
    "dark:border-white/10 dark:bg-transparent dark:text-[#F0EFEC] dark:hover:border-white/40 dark:hover:bg-white/10",
  ghost:
    "bg-transparent text-[#1d1d1d] border border-transparent hover:bg-[#e8fbf6] " +
    "dark:text-[#F0EFEC] dark:hover:bg-white/10",
  danger:
    "bg-[#ef4444] text-white hover:bg-[#dc2626] active:bg-[#b91c1c] border border-transparent dark:bg-[#dc2626] dark:text-white dark:hover:bg-[#ef4444] dark:active:bg-[#b91c1c]",
};

const sizeClasses: Record<ButtonSize, string> = {
  sm: "h-9 px-4 text-sm min-h-[36px]",
  md: "h-11 px-5 text-sm min-h-[44px]",
  lg: "h-12 px-7 text-base min-h-[48px]",
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = "primary", size = "md", loading = false, className, children, disabled, type, ...rest }, ref) => {
    return (
      <button
        ref={ref}
        type={type ?? "button"}
        disabled={disabled || loading}
        className={cn(
          "inline-flex items-center justify-center gap-2 rounded-full font-heading font-semibold",
          "transition-all duration-200 ease-out select-none motion-reduce:transition-none",
          "active:scale-[0.98]",
          "disabled:active:scale-100",
          "focus-visible:outline-2 focus-visible:outline-[#2fdebf] focus-visible:outline-offset-2",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          "shadow-[0_1px_4px_rgba(29,29,29,0.12)]",
          variantClasses[variant],
          sizeClasses[size],
          className,
        )}
        {...rest}
      >
        {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
        {children}
      </button>
    );
  },
);
Button.displayName = "Button";
