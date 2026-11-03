import * as React from "react";
import { Slot } from "radix-ui";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "outline" | "ghost" | "danger";
type Size = "sm" | "md" | "lg" | "icon";

const variants: Record<Variant, string> = {
  primary: "bg-primary text-primary-fg hover:bg-primary-hover shadow-sm",
  secondary: "bg-secondary text-secondary-fg hover:bg-secondary-hover",
  outline: "border border-border-strong bg-surface text-text hover:bg-subtle",
  ghost: "text-muted hover:bg-subtle hover:text-text",
  danger: "bg-danger text-white hover:brightness-110 shadow-sm",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-sm gap-1.5",
  md: "h-9 px-4 text-base gap-2",
  lg: "h-11 px-5 text-lg gap-2",
  icon: "h-9 w-9 justify-center",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  /** Render the child element (e.g. a Link) with button styling. */
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", loading, disabled, asChild, children, ...props }, ref) => {
    const Comp = asChild ? Slot.Root : "button";
    return (
    <Comp
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded-md font-medium transition-colors duration-150",
        "disabled:pointer-events-none disabled:opacity-50 active:translate-y-px",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {asChild ? (
        children
      ) : (
        <>
          {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
          {children}
        </>
      )}
    </Comp>
    );
  },
);
Button.displayName = "Button";

/** Icon-only button. `label` is required so it always has an accessible name. */
export const IconButton = React.forwardRef<
  HTMLButtonElement,
  Omit<ButtonProps, "size" | "children"> & { label: string; children: React.ReactNode }
>(({ label, children, variant = "ghost", className, ...props }, ref) => (
  <Button ref={ref} variant={variant} size="icon" aria-label={label} title={label} className={className} {...props}>
    {children}
  </Button>
));
IconButton.displayName = "IconButton";
