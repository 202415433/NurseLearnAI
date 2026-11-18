import type { ReactNode } from "react";

export function Notice({ tone = "info", title, children }: { tone?: "info" | "warning" | "danger"; title?: string; children: ReactNode }) {
  const styles = {
    info: "border-primary bg-primary-soft",
    warning: "border-warning bg-warning-soft",
    danger: "border-danger bg-danger-soft",
  }[tone];
  return (
    <div role={tone === "danger" ? "alert" : "note"} className={`rounded-md border-l-4 p-4 text-fg ${styles}`}>
      {title ? <p className="mb-1 font-semibold">{title}</p> : null}
      <div>{children}</div>
    </div>
  );
}
