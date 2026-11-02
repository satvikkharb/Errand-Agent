import { useId } from "react";

type FieldProps = React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  hint?: string;
};

export function Field({ label, hint, className = "", ...props }: FieldProps) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-[14px] font-semibold">
        {label}
      </label>
      <input
        id={id}
        {...props}
        className={`h-12 w-full rounded-xl bg-sheet px-4 text-ink outline-none ring-1 ring-line placeholder:text-muted/60 focus:ring-2 focus:ring-ink ${className}`}
      />
      {hint && <p className="mt-1 text-[13px] text-muted">{hint}</p>}
    </div>
  );
}
