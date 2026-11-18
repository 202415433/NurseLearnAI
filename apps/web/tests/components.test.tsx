import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AvailabilityBadge } from "@/components/AvailabilityBadge";
import { Breadcrumbs } from "@/components/Breadcrumbs";
import { ChoiceCard } from "@/components/ChoiceCard";
import { EmptyState } from "@/components/EmptyState";
import { Notice } from "@/components/Notice";
import { Pagination } from "@/components/Pagination";
import { getDictionary } from "@/lib/i18n";

const t = getDictionary("fr");

describe("AvailabilityBadge", () => {
  it("signale un programme en préparation comme non validé", () => {
    render(<AvailabilityBadge availability={{ published: 0, in_preparation: 1 }} t={t.availability} />);
    expect(screen.getByTestId("badge-preparation").textContent).toContain("non encore validé");
    expect(screen.queryByTestId("badge-published")).toBeNull();
  });

  it("affiche les programmes publiés", () => {
    render(<AvailabilityBadge availability={{ published: 2, in_preparation: 0 }} t={t.availability} />);
    expect(screen.getByTestId("badge-published").textContent).toBe("2 programmes disponibles");
  });

  it("indique l'absence de programme", () => {
    render(<AvailabilityBadge availability={{ published: 0, in_preparation: 0 }} t={t.availability} />);
    expect(screen.getByTestId("badge-none")).toBeTruthy();
  });
});

describe("Breadcrumbs", () => {
  it("marque la page courante et relie les étapes précédentes", () => {
    render(
      <Breadcrumbs label="Fil d'Ariane" items={[{ label: "Accueil", href: "/fr" }, { label: "Pays", href: "/fr/pays" }, { label: "Québec" }]} />,
    );
    expect(screen.getByRole("navigation", { name: "Fil d'Ariane" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Pays" }).getAttribute("href")).toBe("/fr/pays");
    expect(screen.getByText("Québec").getAttribute("aria-current")).toBe("page");
  });
});

describe("ChoiceCard", () => {
  it("est un lien unique avec titre et description", () => {
    render(
      <ul>
        <ChoiceCard href="/fr/metiers?j=CA-QC" title="Québec" description="Province" testId="j" />
      </ul>,
    );
    const link = screen.getByTestId("j");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("/fr/metiers?j=CA-QC");
    expect(link.textContent).toContain("Province");
  });
});

describe("Pagination", () => {
  const labels = { label: "Pagination", previous: "Précédent", next: "Suivant", pageOf: t.programs.pageOf };

  it("n'apparaît pas pour une seule page", () => {
    const { container } = render(<Pagination total={5} limit={20} offset={0} buildHref={(o) => `?o=${o}`} labels={labels} />);
    expect(container.innerHTML).toBe("");
  });

  it("calcule les liens précédent et suivant", () => {
    render(<Pagination total={45} limit={20} offset={20} buildHref={(o) => `?o=${o}`} labels={labels} />);
    expect(screen.getByText("Page 2 sur 3")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Précédent" }).getAttribute("href")).toBe("?o=0");
    expect(screen.getByRole("link", { name: "Suivant" }).getAttribute("href")).toBe("?o=40");
  });
});

describe("EmptyState et Notice", () => {
  it("affichent leur contenu avec le bon rôle", () => {
    render(
      <>
        <EmptyState title="Aucun programme" testId="empty">
          Rien n'est publié.
        </EmptyState>
        <Notice tone="danger">Alerte</Notice>
      </>,
    );
    expect(screen.getByTestId("empty").textContent).toContain("Rien n'est publié.");
    expect(screen.getByRole("alert").textContent).toBe("Alerte");
  });
});
