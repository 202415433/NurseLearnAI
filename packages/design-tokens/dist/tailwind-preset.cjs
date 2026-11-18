// Généré par src/build.mjs — preset Tailwind CSS partagé (web).
module.exports = {
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
      "bg": "var(--nl-color-bg)",
      "surface": "var(--nl-color-surface)",
      "surface-raised": "var(--nl-color-surface-raised)",
      "border": "var(--nl-color-border)",
      "border-strong": "var(--nl-color-border-strong)",
      "text": "var(--nl-color-text)",
      "text-muted": "var(--nl-color-text-muted)",
      "primary": "var(--nl-color-primary)",
      "primary-hover": "var(--nl-color-primary-hover)",
      "on-primary": "var(--nl-color-on-primary)",
      "primary-soft": "var(--nl-color-primary-soft)",
      "accent": "var(--nl-color-accent)",
      "accent-fill": "var(--nl-color-accent-fill)",
      "on-accent-fill": "var(--nl-color-on-accent-fill)",
      "accent-soft": "var(--nl-color-accent-soft)",
      "success": "var(--nl-color-success)",
      "warning": "var(--nl-color-warning)",
      "warning-soft": "var(--nl-color-warning-soft)",
      "danger": "var(--nl-color-danger)",
      "danger-soft": "var(--nl-color-danger-soft)",
      "focus": "var(--nl-color-focus)"
},
      fontFamily: { sans: ["var(--nl-font-sans)"], mono: ["var(--nl-font-mono)"] },
      borderRadius: {"sm":"6px","md":"10px","lg":"16px","pill":"999px"},
      boxShadow: {"sm":"0 1px 2px rgba(15, 30, 51, 0.08)","md":"0 4px 12px rgba(15, 30, 51, 0.10)","lg":"0 12px 32px rgba(15, 30, 51, 0.14)"},
      minHeight: { touch: "44px" },
      minWidth: { touch: "44px" },
      maxWidth: { prose: "72ch" },
      transitionTimingFunction: { nl: "cubic-bezier(0.2, 0, 0, 1)" },
    },
  },
};
