import {
  cloneElement,
  forwardRef,
  isValidElement,
  useId,
  type InputHTMLAttributes,
  type ReactElement,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { CircleAlert } from "lucide-react";
import { cn } from "@/core/lib/cn";

const control =
  "w-full rounded-sm border border-control-border bg-surface px-2.5 text-base text-ink placeholder:text-muted " +
  "transition-colors duration-150 hover:border-ink focus-visible:outline-2 focus-visible:outline-offset-0 " +
  "focus-visible:border-focus disabled:cursor-not-allowed disabled:bg-surface-2 disabled:text-muted " +
  "aria-[invalid=true]:border-failed";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(control, "h-8", className)} {...props} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...props }, ref) {
    return <textarea ref={ref} className={cn(control, "min-h-20 py-1.5", className)} {...props} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...props },
  ref,
) {
  return (
    <select ref={ref} className={cn(control, "h-8 pr-7 cursor-pointer", className)} {...props}>
      {children}
    </select>
  );
});

interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  /** Validation message. Shown (and linked via aria-describedby) only when `showError` is true. */
  error?: string;
  /** True once the user left the field or tried to submit – avoids premature errors. */
  showError?: boolean;
  required?: boolean;
  className?: string;
  children: ReactElement<{ id?: string; "aria-describedby"?: string; "aria-invalid"?: boolean | "true" | "false" }>;
}

/**
 * Label + control + hint + error. The control starts with aria-invalid="false" and the
 * error id is added to aria-describedby only while the error is shown, so screen readers
 * do not announce hidden errors.
 */
export function Field({ label, hint, error, showError = false, required, className, children }: FieldProps) {
  const generated = useId();
  // Keep an id the caller set on the control (e.g. to move focus to it); otherwise generate one.
  const own = isValidElement<{ id?: string }>(children) ? children.props.id : undefined;
  const id = own ?? generated;
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const invalid = Boolean(error && showError);
  const describedBy = [hint ? hintId : null, invalid ? errorId : null].filter(Boolean).join(" ") || undefined;
  const child = isValidElement(children)
    ? cloneElement(children, { id, "aria-describedby": describedBy, "aria-invalid": invalid ? "true" : "false" })
    : children;
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-sm font-medium text-ink">
        {label}
        {required && (
          <span className="text-failed" aria-hidden="true">
            {" "}
            *
          </span>
        )}
      </label>
      {child}
      {hint && (
        <p id={hintId} className="text-xs text-muted">
          {hint}
        </p>
      )}
      <p id={errorId} className="field-error items-center gap-1 text-xs text-failed" data-visible={invalid}>
        <CircleAlert className="size-3.5 shrink-0" aria-hidden="true" />
        {error}
      </p>
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-2.5">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-describedby={description ? `${id}-d` : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative mt-0.5 inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors duration-150",
          checked ? "border-primary bg-primary" : "border-control-border bg-surface-2",
          "disabled:cursor-not-allowed disabled:opacity-55",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "inline-block size-3.5 rounded-full shadow-sm transition-transform duration-150",
            checked ? "translate-x-[18px] bg-on-primary" : "translate-x-[2px] bg-muted",
          )}
        />
      </button>
      <div className="flex flex-col">
        <label htmlFor={id} className="cursor-pointer text-sm font-medium">
          {label}
        </label>
        {description && (
          <span id={`${id}-d`} className="text-xs text-muted">
            {description}
          </span>
        )}
      </div>
    </div>
  );
}
