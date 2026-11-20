import type { ReactNode } from "react";

import { Notice } from "./Notice";

// Champs de formulaire accessibles : étiquette visible, aide reliée (aria-describedby), cible ≥ 44 px.
export function Field({
  name,
  id,
  label,
  type = "text",
  autoComplete,
  hint,
  required = true,
  defaultValue,
  minLength,
  maxLength,
}: {
  name: string;
  id?: string;
  label: string;
  type?: string;
  autoComplete?: string;
  hint?: string;
  required?: boolean;
  defaultValue?: string;
  minLength?: number;
  maxLength?: number;
}) {
  const inputId = id ?? name;
  const hintId = hint ? `${inputId}-aide` : undefined;
  return (
    <div className="space-y-1">
      <label htmlFor={inputId} className="block font-semibold">
        {label}
      </label>
      <input
        id={inputId}
        name={name}
        type={type}
        autoComplete={autoComplete}
        required={required}
        defaultValue={defaultValue}
        minLength={minLength}
        maxLength={maxLength}
        aria-describedby={hintId}
        className="block min-h-11 w-full rounded-md border border-border-strong bg-bg px-3 text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      />
      {hint ? (
        <p id={hintId} className="text-sm text-fg-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function Checkbox({ name, children, testId }: { name: string; children: ReactNode; testId?: string }) {
  return (
    <div className="flex items-start gap-3">
      <input
        id={name}
        name={name}
        type="checkbox"
        required
        data-testid={testId}
        className="mt-1 h-5 w-5 shrink-0 accent-[var(--color-primary)]"
      />
      <label htmlFor={name}>{children}</label>
    </div>
  );
}

export function SubmitButton({
  children,
  variant = "primary",
  testId,
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "danger";
  testId?: string;
}) {
  const styles = {
    primary: "bg-primary text-on-primary hover:bg-primary-hover",
    secondary: "border border-border-strong bg-surface text-fg hover:border-primary",
    danger: "border border-danger bg-surface text-danger hover:bg-danger-soft",
  }[variant];
  return (
    <button
      type="submit"
      data-testid={testId}
      className={`inline-flex min-h-11 items-center rounded-md px-5 font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${styles}`}
    >
      {children}
    </button>
  );
}

// Messages d'état ou d'erreur : seuls les codes connus du dictionnaire sont affichés.
export function FormStatus({
  status,
  error,
  messages,
  errors,
}: {
  status?: string;
  error?: string;
  messages: Record<string, string>;
  errors: Record<string, string>;
}) {
  if (error) {
    return (
      <div data-testid="form-error">
        <Notice tone="danger">{errors[error] ?? errors.generic}</Notice>
      </div>
    );
  }
  if (status && messages[status]) {
    return (
      <div role="status" data-testid="form-status">
        <Notice>{messages[status]}</Notice>
      </div>
    );
  }
  return null;
}

export function FormCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-surface p-6">
      <h2 className="mb-4 text-xl font-semibold">{title}</h2>
      {children}
    </section>
  );
}
