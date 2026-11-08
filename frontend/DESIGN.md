# finding nemo: design reference (from Allen, 2026-11-08)

This is the art direction for the rebuild. `PLAN.md` covers structure and contracts; this file covers the look.
If they disagree on the look, this file wins. If anything here disagrees with `CLAUDE.md` on behaviour or contracts, `CLAUDE.md` wins.

## 1. The hero reference image (definitive for Page 1)

The reference is a painted overhead view of a koi pond. The original screenshot is `design/pond-reference.png` when present. Until then, this description is the reference. Percentages are of viewport width (x) and height (y), measured from the top-left.

**Overall feel:** watercolor and painterly, soft-edged, faded and luminous. It is Neo-Ghibli and cozy: no hard outlines, no photorealism, no dark areas, no neon.

**Water**
- Bright, faded turquoise to sky blue, roughly `#A9D9E2` to `#CBE8EE`.
- It is lighter and sunlit toward the top-centre, with a soft diagonal light shaft from the upper left.
- A web of brighter caustic lines (pale cream-white) covers the whole surface and drifts slowly.

**Pond bed (seen through the water)**
- Rounded cobblestones and pebbles in a slightly deeper blue, softly mottled, across the whole floor.
- Larger mossy grey-green boulders sit in a cluster at the bottom-left (x 0–30%, y 60–100%) and one or two at the bottom-centre-right (x 45–55%, y 80–100%).
- The water tints everything below it.

**Koi**
- Two small koi, each about 8–9% of viewport width long. Do not make them bigger.
- Kohaku pattern: white bodies with faded coral-orange patches (`#E8875F`), translucent white fins, and long flowing tails.
- Koi A is in the upper left, near (24%, 30%), heading up and right with its tail trailing toward the lower left.
- Koi B is in the lower right, near (77%, 65%), heading left and down.
- Each koi has faint concentric ripple rings around it.

**Seaweed**
- Long ribbon-like translucent green plants, from sage to olive (`#7FA67A` to `#A9BCA5`).
- They frame the left edge, rising from the bottom-left to about y 30%. Some grow from the bottom centre (x 33–42%).
- More frame the right edge, from the bottom-right up to the top-right, behind the log.
- They curl, overlap and sway.

**Lily pads**
- Flat, pale yellow-green (`#B5CF8E` to `#9DBF7A`) notched discs with soft radial veins, floating on the surface and casting faint shadows.
- A cluster of 3–4 at the top-left (x 0–22%, y 5–25%).
- One beside the log on the right (around 83%, 27%).
- Two at the bottom-left (x 5–25%, y 80–100%).
- Two or three larger ones at the bottom-right (x 85–100%, y 75–100%).
- A few tiny floating leaves are scattered around (near 21% 52%, 88% 49% and 79% 88%).

**Log**
- A weathered grey-brown wooden log with green moss patches.
- It lies diagonally in the upper right, from about (64%, 5%) to beyond the right edge at (100%, 35%), and runs off-screen.
- Faint ripple rings surround its left end, and it bobs and rotates very slightly.

**Centre**
- The middle (x 30–70%, y 25–65%) is relatively empty and luminous so the title and buttons read clearly.
- The koi must not swim through the title or button zone.

## 2. Hero UI, as HTML over the canvas (never baked into the scene)

- **Logo**, top-left: "finding nemo" in a rounded display font (Fredoka), deep slate blue `#35596B`, about 28px, with a small double wave mark under "nemo" in muted blue.
- **Profile button**, top-right: a small pale translucent-white circle with a slate person icon. It is static, because no auth exists.
- **Title**: "finding nemo", large (about 6–7vw, clamped), Fredoka, `#2F5872`. It is centred horizontally, slightly above the vertical midpoint (its centre near y 38%).
- **Wave mark**: two stacked wavy strokes in muted blue (`#5F8FB0`) under the title, about 14% of the width.
- **Buttons**, side by side near y 57%, with a gap of about 3vw:
  - **Primary**: "Start Project →". A pill in muted water blue (`#4F80B0`) with cream-white text, a soft white rim and a subtle hover lift.
  - **Secondary**: "Past Projects" with a folder icon at its left. A pill in translucent warm cream (`#F3EFE4` at about 85%), with muted slate-blue text and a blue folder icon.
- There is no subtitle and no paragraph.

## 3. Global art direction (from the spec)

- **Aesthetic:** Neo-Ghibli / Cozy Web / Atmospheric Developer Studio. Calm, dreamlike, organic, soft, premium, slightly whimsical, technically sophisticated. Do not copy copyrighted characters or scenes.
- **Palette:**
  - Backgrounds: mist `#DCEEF1`, pale water `#BBDCE3`, washed sky `#D5E8EB`, soft aqua `#A9D2D5`.
  - Secondary: sage `#A9BCA5`, sea glass `#8DB9B7`, muted teal `#638F91`, soft stone `#D7D1C0`.
  - Text: `#35596B`, `#526F7C`, `#8199A0`.
  - Accents: coral `#D9937E`, cream `#F0EBDD`.
- **Avoid:** pure white or black backgrounds, neon, saturated pastels, purple AI gradients, heavy glassmorphism, dark-mode dashboards.
- **Texture:** subtle watercolor grain, atmospheric gradients, mist, organic edges and soft shadows, always keeping text readable.
- **Type:**
  - Fredoka for the hero and logo only.
  - Nunito Sans for the UI.
  - IBM Plex Mono for technical values: coordinates, weights, layer ids and traces.
  - Technical pages stay restrained and use no oversized type.
- **Header:**
  - The logo on the left goes to Home and never deletes saved projects.
  - A profile indicator sits on the right.
  - There are no nav links.

## 4. Page 2: Sources

- **Background:** watercolor sky blue with soft white mist, cloudy gradients and light paper grain. There is no 3D pond here.
- **Header:** a back button (to Home) and the title "Sources".
- **Intent input:** one large horizontal rectangle with the placeholder `describe intent for application|`, where the trailing bar blinks like a caret. Typing hides the placeholder, and the field grows naturally. It must not look like a chat interface.
- **Suggestions:** after the dev describes a source, show two or more suggestions from the real `POST /v1/apps/{app}/sources/suggest`. Clearly distinguish push sources (the dev's app POSTs with an ingest key) from pull sources (the platform polls an RSS or JSON connector).
- **Selecting a suggestion:**
  1. Add it to the selected sources.
  2. Show it as a chip in the bottom bar.
  3. Clear the input back to its placeholder.
  4. Clear the suggestions.
  5. Let the dev describe another source.
  - Multiple sources are allowed and each can be removed, with subtle animation.
- **Bottom bar:** persistent, with chips, a count, and Continue on the right.
  - Continue stays disabled until the config is valid.
  - Clicking it validates, runs the real compile and moves to Architecture with the proposed AppSpec.
  - It never activates the app.

## 5. Page 3: Architecture / Layer inspector

- Two panels and a footer, in the faded watercolor style. It should read as a sophisticated observability console, but not in dark mode.
- **Left panel: heatmap**
  - Soft blue and cyan cells, warm faded coral hotspots, subtle grid lines, a soft glow on high values and smooth transitions, with small mono labels for coordinates and metadata.
  - It uses the real `MapResponse` after activation. Before activation it shows a clearly labelled "not yet live" schematic and never invents values.
  - It shows the current score, the active hotspot count, the last update, grid coordinates and the app state.
- **Right panel: layer stack**
  - Each layer shows its id, operator, weight, tier, visibility, status and a short description.
  - Locked, stiff and flexible must be instantly distinguishable, and a locked weight must never look editable.
  - Selecting a layer highlights it, highlights its contribution on the heatmap, and shows its operator, sources, weight, tier and any trace.
- **Connectors**
  - Organic, thin SVG wires in muted teal: source → operator → layer → combined score, with an explanatory card generated from the real input and operator.
  - They animate subtly on selection, branch naturally, avoid crossings and show the direction of flow.
- **Score:** show `score = clamp(Σ wᵢ · norm(layerᵢ), 0, 1)`, with authority overrides applied after the sum. Never suggest that agents change the score directly.
- **Confirmation:**
  - An explicit review step shows the proposed tiers, verification methods, visibility, operators, weights and plausibility.
  - The dev can edit and save through `PUT /spec`, then confirm through `/activate`.
  - Keys are shown once.
- **Footer:**
  - **Save Project** stores the project to localStorage and confirms visibly.
  - **Go Back to Editing** returns to Sources with the draft intact.

## 6. Motion

- **Home → Sources:** the background fades, the content moves up a little, then the new page fades in.
- **Sources → Architecture:** a subtle horizontal move, panels staggered in, and heatmap cells fading in progressively.
- **Buttons:** a subtle spring, a gentle lift and soft shadows. No exaggerated bounce.
- **Reduced motion:** respect `prefers-reduced-motion`. Non-essential animation is reduced or turned off, and everything still works.

## 7. Performance and responsiveness

- **Animation loop:** animate through refs and shader uniforms inside `useFrame`, never with React state each frame.
- **Rendering:**
  - Reuse geometries and materials, and dispose of them on unmount.
  - Cap dpr at 1.75.
  - Pause when the tab is hidden.
  - Show a static fallback without WebGL.
  - On mobile, render fewer decorative objects.
- **Screen targets:** desktop first, then tablet with stacked or resized panels, then mobile with stacked panels and every function still available.
