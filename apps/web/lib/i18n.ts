// Chaînes d'interface FR/EN. Ajouter une langue = ajouter un dictionnaire de même forme.
export const locales = ["fr", "en"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "fr";

export function isLocale(value: string): value is Locale {
  return (locales as readonly string[]).includes(value);
}

const fr = {
  meta: {
    title: "NurseLearn AI — Formation en soins infirmiers et assistance aux soins",
    description: "Apprenez les soins infirmiers et l'assistance aux soins selon le référentiel de votre pays.",
  },
  a11y: { skip: "Aller au contenu", mainNav: "Navigation principale", language: "Langue" },
  theme: { toDark: "Activer le thème sombre", toLight: "Activer le thème clair", dark: "Sombre", light: "Clair" },
  nav: { home: "Accueil", start: "Choisir mon parcours" },
  disclaimer:
    "NurseLearn AI est une plateforme de formation. Ses attestations internes ne sont ni un diplôme, ni une licence, ni une autorisation d'exercer.",
  home: {
    title: "Apprenez les soins, à votre rythme, selon le référentiel de votre pays",
    lead: "Des parcours structurés du niveau débutant à la préparation professionnelle, pour l'assistance aux soins et les soins infirmiers.",
    cta: "Commencer",
    stepsTitle: "Comment ça marche",
    steps: [
      { title: "Choisissez votre pays ou province", text: "Les métiers et les actes permis diffèrent d'une juridiction à l'autre." },
      { title: "Choisissez votre métier", text: "Préposé aux bénéficiaires, aide-soignant, infirmier auxiliaire… aucun titre n'est considéré équivalent à un autre." },
      { title: "Suivez les formations publiées", text: "Chaque cours est relu par un professionnel qualifié avant publication." },
    ],
    noticeTitle: "À savoir",
  },
  jurisdictions: {
    title: "Choisissez votre pays ou votre province",
    lead: "Le contenu, les métiers et les actes permis sont adaptés à ce référentiel.",
    groups: { province: "Provinces", country: "Pays", framework: "Autres référentiels" },
  },
  professions: {
    title: "Choisissez votre métier",
    in: "Référentiel",
    change: "Changer de pays ou de province",
    lead: "Les titres ne sont jamais considérés comme équivalents d'un pays à l'autre.",
    regulated: "Profession réglementée",
    notRegulated: "Profession non réglementée par un ordre professionnel",
    regulator: "Organisme",
    pending: "Informations en cours de validation par un professionnel",
    see: "Voir les formations",
    categories: {
      care_assistant: "Assistance aux soins",
      practical_nurse: "Soins infirmiers auxiliaires",
      registered_nurse: "Soins infirmiers",
      other: "Autre",
    } as Record<string, string>,
    missing: "Aucun pays ou province sélectionné.",
  },
  catalog: {
    title: "Formations",
    for: "Pour",
    emptyTitle: "Contenu en préparation",
    emptyText:
      "Aucune formation n'est encore publiée pour ce métier. Chaque cours doit d'abord être rédigé puis validé par un professionnel qualifié : nous ne publions rien avant cette relecture.",
    back: "Changer de métier",
    levels: {
      beginner: "Débutant",
      fundamental: "Fondamental",
      intermediate: "Intermédiaire",
      advanced: "Avancé",
      professional_prep: "Préparation professionnelle",
    } as Record<string, string>,
    tiers: { free: "Gratuit", premium: "Premium", organization: "Établissement" } as Record<string, string>,
  },
  errors: {
    unavailable: "Le service est momentanément indisponible. Réessayez dans quelques instants.",
    notFound: "Élément introuvable.",
  },
};

export type Dictionary = typeof fr;

const en: Dictionary = {
  meta: {
    title: "NurseLearn AI — Nursing and care assistance training",
    description: "Learn nursing and care assistance according to your country's framework.",
  },
  a11y: { skip: "Skip to content", mainNav: "Main navigation", language: "Language" },
  theme: { toDark: "Switch to dark theme", toLight: "Switch to light theme", dark: "Dark", light: "Light" },
  nav: { home: "Home", start: "Choose my path" },
  disclaimer:
    "NurseLearn AI is a training platform. Its internal attestations are not a diploma, a licence or an authorization to practise.",
  home: {
    title: "Learn care at your own pace, following your country's framework",
    lead: "Structured paths from beginner to professional preparation, for care assistance and nursing.",
    cta: "Get started",
    stepsTitle: "How it works",
    steps: [
      { title: "Choose your country or province", text: "Occupations and permitted acts differ from one jurisdiction to another." },
      { title: "Choose your occupation", text: "Patient attendant, healthcare assistant, practical nurse… no title is treated as equivalent to another." },
      { title: "Take published courses", text: "Every course is reviewed by a qualified professional before publication." },
    ],
    noticeTitle: "Good to know",
  },
  jurisdictions: {
    title: "Choose your country or province",
    lead: "Content, occupations and permitted acts are adapted to this framework.",
    groups: { province: "Provinces", country: "Countries", framework: "Other frameworks" },
  },
  professions: {
    title: "Choose your occupation",
    in: "Framework",
    change: "Change country or province",
    lead: "Titles are never treated as equivalent from one country to another.",
    regulated: "Regulated profession",
    notRegulated: "Not regulated by a professional order",
    regulator: "Regulator",
    pending: "Information pending validation by a professional",
    see: "See courses",
    categories: {
      care_assistant: "Care assistance",
      practical_nurse: "Practical nursing",
      registered_nurse: "Nursing",
      other: "Other",
    },
    missing: "No country or province selected.",
  },
  catalog: {
    title: "Courses",
    for: "For",
    emptyTitle: "Content in preparation",
    emptyText:
      "No course is published yet for this occupation. Every course must first be written and then validated by a qualified professional: nothing is published before that review.",
    back: "Change occupation",
    levels: {
      beginner: "Beginner",
      fundamental: "Fundamental",
      intermediate: "Intermediate",
      advanced: "Advanced",
      professional_prep: "Professional preparation",
    },
    tiers: { free: "Free", premium: "Premium", organization: "Organization" },
  },
  errors: {
    unavailable: "The service is temporarily unavailable. Please try again in a moment.",
    notFound: "Not found.",
  },
};

const dictionaries: Record<Locale, Dictionary> = { fr, en };

export function getDictionary(locale: Locale): Dictionary {
  return dictionaries[locale];
}
