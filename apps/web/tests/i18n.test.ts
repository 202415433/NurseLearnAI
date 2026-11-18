// @vitest-environment node
import { describe, expect, it } from "vitest";

import { getDictionary, isLocale, locales } from "@/lib/i18n";

function shape(value: unknown, path = ""): string[] {
  if (typeof value === "function") return [`${path}:fn`];
  if (Array.isArray(value)) return [`${path}:array${value.length}`, ...value.flatMap((v, i) => shape(v, `${path}[${i}]`))];
  if (value && typeof value === "object")
    return Object.entries(value).flatMap(([k, v]) => shape(v, path ? `${path}.${k}` : k));
  return [`${path}:${typeof value}`];
}

describe("dictionnaires", () => {
  it("ont exactement les mêmes clés dans chaque langue", () => {
    const reference = shape(getDictionary("fr")).sort();
    for (const locale of locales) expect(shape(getDictionary(locale)).sort()).toEqual(reference);
  });

  it("n'ont aucune chaîne vide", () => {
    expect(JSON.stringify(getDictionary("fr"))).not.toContain('""');
    expect(JSON.stringify(getDictionary("en"))).not.toContain('""');
  });

  it("rappellent que la formation n'est ni un diplôme ni une autorisation d'exercer", () => {
    expect(getDictionary("fr").disclaimer).toMatch(/ni un diplôme.*ni une autorisation d'exercer/);
    expect(getDictionary("en").disclaimer).toMatch(/not a diploma.*authorization to practise/);
  });

  it("accordent les nombres", () => {
    const t = getDictionary("fr");
    expect(t.availability.published(1)).toBe("1 programme disponible");
    expect(t.availability.published(3)).toBe("3 programmes disponibles");
    expect(t.availability.inPreparation(1)).toContain("non encore validé");
  });

  it("valident les langues", () => {
    expect(isLocale("fr")).toBe(true);
    expect(isLocale("de")).toBe(false);
  });
});
