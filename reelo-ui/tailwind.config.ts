import type { Config } from "tailwindcss";

/**
 * Tailwind is wired up for future component work. The ported screens rely on the
 * canonical design system in `app/globals.css` (the prototype's styles.css), so
 * here we just map the design tokens to Tailwind theme values referencing the
 * same CSS variables — use these utilities for any NEW components you add.
 */
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./screens/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: "var(--brand)",
        "brand-600": "var(--brand-600)",
        "brand-700": "var(--brand-700)",
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        "surface-3": "var(--surface-3)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        text: "var(--text)",
        "text-2": "var(--text-2)",
        "text-3": "var(--text-3)",
      },
      borderRadius: {
        sm: "9px",
        md: "13px",
        lg: "18px",
        xl: "24px",
      },
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "monospace"],
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
      },
    },
  },
  plugins: [],
};

export default config;
