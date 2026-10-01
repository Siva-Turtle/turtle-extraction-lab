import type { Config } from "tailwindcss";

// Same v2 design tokens as the CRM prototype (turtle-crm/frontend/tailwind.config.ts).
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: "#2edebe",
          50: "#e8fbf7",
          100: "#c6f5ec",
          200: "#9deedd",
          300: "#6fe3cd",
          400: "#4ad9bf",
          500: "#2edebe",
          600: "#1fc7a8",
          700: "#17a68b",
          800: "#14705f",
          900: "#134a41",
        },
        ink: {
          DEFAULT: "#1d1d1d",
          50: "#f7f7f7",
          100: "#f0f0f0",
          200: "#d9d9d9",
          400: "#7a7a7a",
          500: "#4b4b4b",
          900: "#1d1d1d",
        },
        surface: "#ffffff",
        success: "#22c55e",
        warning: "#f59e0b",
        danger: "#ef4444",
        info: "#3b82f6",
        turtle: {
          DEFAULT: "#2fdebf",
          deep: "#0d5c4a",
          soft: "#e8fbf6",
          ink: "#1d1d1d",
          page: "#eef0f4",
          line: "#e5e7eb",
          muted: "#4a5058",
          muted2: "#8a8f98",
        },
      },
      fontFamily: {
        display: ["Comfortaa", "cursive"],
        heading: ["Montserrat", "sans-serif"],
        sans: ['"Open Sans"', "sans-serif"],
        module: ['"Plus Jakarta Sans"', "Montserrat", "sans-serif"],
      },
      borderRadius: {
        xl: "12px",
      },
      boxShadow: {
        float: "0 8px 24px rgba(29, 29, 29, 0.08)",
        sheet: "0 -8px 32px rgba(29, 29, 29, 0.12)",
      },
    },
  },
  plugins: [],
};

export default config;
