import { cn } from "@/core/lib/cn";

/** Toggle-button filter chip (use with aria-pressed); `small` for dense filter rows. */
export const chip = (on: boolean, small = false) =>
  cn(
    small ? "h-7" : "h-8",
    "cursor-pointer rounded-full border px-3 text-sm transition-colors duration-150",
    on ? "border-primary bg-primary-soft font-medium text-primary" : "border-line-strong bg-surface hover:bg-surface-2",
  );
