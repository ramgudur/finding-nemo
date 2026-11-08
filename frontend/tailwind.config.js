/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Colours come from CSS variables (src/styles/tokens.css): :root holds the Home/EndUser values, and
      // .wp (the working pages) swaps in the working palette. Never put a raw hex in a component.
      colors: Object.fromEntries(
        Object.entries({
          mist: "mist",
          paleWater: "pale-water",
          washedSky: "washed-sky",
          softAqua: "soft-aqua",
          sage: "sage",
          seaGlass: "sea-glass",
          mutedTeal: "muted-teal",
          softStone: "soft-stone",
          slate: "slate",
          ink: "ink",
          inkSoft: "ink-soft",
          coral: "coral",
          danger: "danger",
          primary: "primary",
          primaryHover: "primary-hover",
          cream: "cream",
          sheet: "sheet",
          sheet2: "sheet-2",
          deep: "deep",
          deep2: "deep-2",
          attn: "attn",
          attnBg: "attn-bg",
          live: "live",
          tierLocked: "tier-locked",
          tierStiff: "tier-stiff",
          tierFlex: "tier-flex",
          heat5: "heat-5",
          onDeep: "on-deep",
          onDeep2: "on-deep-2",
          attnOnDeep: "attn-on-deep",
          okOnDeep: "ok-on-deep",
        }).map(([k, v]) => [k, `rgb(var(--c-${v}) / <alpha-value>)`]),
      ),
      transitionTimingFunction: { wp: "cubic-bezier(0.23, 1, 0.32, 1)" },
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
