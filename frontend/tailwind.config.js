/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        mist: "#DCEEF1",
        paleWater: "#BBDCE3",
        washedSky: "#D5E8EB",
        softAqua: "#A9D2D5",
        sage: "#A9BCA5",
        seaGlass: "#8DB9B7",
        mutedTeal: "#638F91",
        softStone: "#D7D1C0",
        slate: "#35596B",
        // Text tokens darkened for WCAG AA on the mist / sky / cream surfaces (>= 4.5:1 for small text).
        ink: "#45606C",
        inkSoft: "#4F6A74",
        coral: "#D9937E",
        // Accessible error text (coral itself is decoration only: ~2.3:1 on cream).
        danger: "#8E4F3C",
        // Primary fill: cream text on it is ~4.9:1.
        primary: "#3B6895",
        primaryHover: "#335C85",
        cream: "#F0EBDD",
      },
      fontFamily: {
        display: ["Fredoka", "ui-rounded", "sans-serif"],
        sans: ["Nunito Sans", "system-ui", "sans-serif"],
        mono: ["IBM Plex Mono", "ui-monospace", "monospace"],
      },
      keyframes: {
        "cursor-blink": {
          "0%, 49%": { opacity: "1" },
          "50%, 100%": { opacity: "0" },
        },
      },
      animation: {
        "cursor-blink": "cursor-blink 1s step-end infinite",
      },
    },
  },
  plugins: [],
};
