---
name: flwn-design
description: Applies the Flwn visual identity (cream dotted canvas, five pastel accents, navy logo, bold plain headlines, flwn wordmark logo with gold favicon, hand-drawn architecture diagrams) to all three Flwn surfaces - frontend (React UI, components, Tailwind, CSS tokens), docs (README, specs, proposals, architecture pages) and presentations (decks, pitch and graduation slides). Use when building, restyling or reviewing anything Flwn-branded so it matches the Flwn workspace.
---

# Flwn Design

Every Flwn surface must look like it came from the same board. Palette reference: `palette-final.png`. Logo reference: `logo/` (section 6c). Note: `mockup/` predates palette v2. Use the tokens in this file, not the mockup's colors.

## 0. Surfaces

Same tokens, same palette, same tone on all three.

| Surface | Use | Sections |
|---|---|---|
| Frontend | React app, components, Tailwind, CSS | 2, 3, 4, 5, 8, 9, 10 |
| Docs | README, specs, proposals, architecture pages | 2, 3, 6, 6a, 6c, 7 |
| Presentations | Decks, pitch, graduation slides | 2, 3, 6a, 6b, 6c, 7 |

Diagrams (6a) and the logo (6c) appear on all three.

## 1. Feel

- Warm, calm, playful-but-professional. Paper canvas, soft pastel blocks, one dark anchor.
- Flat color blocks. Soft shadows. No gradients, no glassmorphism, no neon.
- Generous spacing. Big bold headline, small quiet metadata.
- Sentence-style headlines ending in a period. Example: "What the team is moving."

## 2. Tokens

Palette v2 is owner-approved. Do not add, shift or "improve" colors. Changes go through the owner.

### Palette (colors)

![Flwn palette](palette/palette.png)

| Group | Name | Hex | Role |
|---|---|---|---|
| Base | Cream | `#F7F3EA` | page background |
| Base | White | `#FFFFFF` | surfaces, tables |
| Base | Navy | `#001F55` | wordmark, dark anchor |
| Base | Ink | `#14131A` | text |
| Base | Ink soft | `#6B6875` | secondary text |
| Base | Border | `#E0DDD5` | lines, frames |
| Pastel | Green | `#B3E3C4` | tip |
| Pastel | Lavender | `#E3D3FA` | decision |
| Pastel | Gold | `#F8DB99` | note, favicon |
| Pastel | Salmon | `#F5B3AC` | warning |
| Pastel | Blue | `#ABE1EA` | info |

Also in `palette/palette.svg` and `palette/tokens.css`.

```css
:root {
  /* base */
  --cream: #F7F3EA;          /* page background */
  --white: #FFFFFF;
  --navy: #001F55;           /* logo, wordmark, dark anchor */
  --ink: #14131A;            /* text */
  --ink-soft: #6B6875;       /* secondary text on cream or white only */

  /* pastel accents */
  --pastel-green: #B3E3C4;
  --pastel-lavender: #E3D3FA;
  --pastel-gold: #F8DB99;    /* UI accent, favicon circle */
  --pastel-salmon: #F5B3AC;
  --pastel-blue: #ABE1EA;

  /* derived neutrals */
  --border: #E0DDD5;         /* 10% ink over cream */
  --dot: #D3CFC9;            /* dotted grid, 16% ink over cream */
  --ink-72: rgba(20, 19, 26, .72);   /* secondary text on pastel cards */

  /* shape */
  --r-sm: 6px;               /* chips */
  --r: 10px;                 /* cards, columns, buttons */
  --shadow: 0 2px 0 rgba(20,19,26,.06), 0 8px 18px rgba(20,19,26,.08);
}
```

Retired: purple `#D9C7F2` (now lavender), gold `#F3C868` (now `#F8DB99`).

Contrast (computed, WCAG relative luminance):
- Ink on pastels: green 12.9, lavender 13.2, gold 13.7, salmon 10.5, blue 12.9. Ink on cream 16.7, on white 18.5.
- Navy on pastels: green 11.1, lavender 11.3, gold 11.7, salmon 9.0, blue 11.1. Navy on cream 14.3, on white 15.8.
- Ink soft on cream 4.9. On pastels only 3.1 to 3.8, so never use it there. Use `--ink-72` on pastel cards.
- Pastel on pastel is about 1.0 to 1.1. Never rely on one pastel to separate from another.

Dark mode: not defined. Do not invent one unless asked.

## 3. Type

- Font: Plus Jakarta Sans, fallback Inter, system-ui. Mono for code: JetBrains Mono.
- Page headline: 40-48px, weight 700, tracking -0.01em, ink.
- Eyebrow: 13-14px, weight 600, uppercase, letter-spacing .12em, `--ink-soft`.
- Card title: 18-20px, weight 600.
- Column title: 18px, weight 600, count 15px `--ink-soft`.
- Meta: 14-15px, `--ink-72` on pastel, `--ink-soft` on cream or white.
- Chips: 12px, weight 600.

## 4. Components

**Canvas.** `--cream` with dotted grid (`radial-gradient(circle, var(--dot) 1.3px, transparent 1.4px)`, 24px). Thin 1.5px `--border` rounded frame (inset 8px, radius 14).

**Task card.** Solid pastel fill, no border, radius 10, padding 24, `--shadow`, min-height 178.
- Top row: white category chip (Research, Design, Product, Engineering) left, `...` menu right in `--ink-72`.
- Title below, margin-top 18.
- Optional 4px progress bar: track `rgba(255,255,255,.6)`, fill `--ink`.
- Footer: 34px white initials avatar, date, message count right-aligned (`4 msg`).
- "Today" = white pill, ink, weight 700. No red anywhere.

**Column header.** White, 1.5px `--border`, radius 10, height 54. 12px rounded-square dot (pastel) + title + count + `+` right.
- Backlog = gold, Planned = blue, In progress = salmon.

**Add task.** Dashed 1.5px border at ink 35%, transparent, centered `+ Add task` in `--ink-soft`.

**AI banner.** Gold strip, radius 12, 44px navy circle `AI` in cream, bold "Daily sum-up", one-line summary in `--ink-72`, ellipsis. Top center.

**Workspace switcher.** Blue block, radius 8, label + chevron, overlaps the banner's right edge.

**Primary button.** `--ink` fill, `--cream` text, radius 10, 24px text. Example: "Meet". This plus the AI avatar are the only dark elements. Navy may replace ink for the fill, never both on one view.

**Avatar stack.** 30px pastel circles, 2-letter initials, 1.5px cream ring, overlapped slightly.

**Side tabs.** Pennant shapes pinned to the left edge, stacked, gold / salmon / green, ink icons only (pointer, frame, comment).

**User badge.** Bottom-left, 52px lavender circle, navy initials, white 2px ring, `--shadow`.

## 5. Layout

- Page: eyebrow, headline, then board. 3 equal columns, 40px gaps. Max 4 columns.
- Cards stack with 20px gap.
- Top-right cluster: avatars + primary action.
- Left rail minimal: 3-5 tabs max.

## 6. Docs and README

- Page bg `--cream`, body text `--ink`, secondary `--ink-soft`.
- Each doc starts with an eyebrow (section name) plus a bold sentence-style title with a period.
- Callouts = pastel blocks, radius 10, no border: green = tip, gold = note, salmon = warning, blue = info, lavender = decision.
- Tables: white surface, 1.5px `--border`, no zebra, header weight 600.
- Code blocks: `--ink` bg, cream text, radius 10.
- Diagrams: section 6a. Slides: 6b. Logo: 6c.
- README: no emoji. Short sentences. Same tone as UI copy.

### Hard rules for docs (check before you finish)

- **Logo = wordmark only.** Take it from `logo/flwn-wordmark-navy.svg`. Never reuse a logo file already in the repo (for example `docs/assets/flwn-logo-primary.svg`): it may include the gold favicon mark. The favicon never appears beside the wordmark or anywhere in a doc body.
- **Padding on every page.** A PDF or printed doc (A4) uses `@page { size: A4; margin: 0 }` and a page block with its own cream background and padding: 16mm left and right, 14-16mm top and bottom. Never rely on the browser's default page margin: it leaves a white border around the cream. On screen, the frame keeps 56px padding (16px on mobile).
- **Verify visually.** Render the PDF to an image, look at every page, and fix overlapping diagram labels, clipped content and wrong page counts before handing it over.

## 6a. Diagrams (hand-drawn architecture style)

Every architecture, flow and data diagram in Flwn docs and slides uses this style. Reference: the Frontend / Backend (Go) / AI Engine / Data Engine system map. **Diagrams use only the section 2 palette.** No other hex values, no extra hues. Before you finish, list every hex in the SVG and confirm each is in section 2.

### Look

- Hand-drawn, Excalidraw look. Font: Excalifont (fallback Virgil, "Comic Neue"). Roughness low, stroke 2px, rounded corners.
- Layered zones: large rounded rectangles, pale tint fill, 1px lighter border. Zone title top-left, uppercase, in the zone's dark hue, optional ownership rule after a colon (`BACKEND (Go): all connections + sockets, owns no data`).
- Nodes: rounded rectangles, flat pastel fill, no outline, `--ink` text.
- Node text = title line + 1-2 short descriptor lines, centered. Max 3 lines.
- Stores (DBs, vector stores) = cylinders.
- Sub-groups = dashed rounded box with small label top-left.
- Cross-cutting rules = dashed rounded box at the bottom of the zone.
- Canvas behind diagram: transparent or `--cream`. No dot grid inside diagrams.

### Zone + node palette (section 2 colors only)

- Zones: `--white` fill, no outline, zone title in `--ink-soft`. Nested sub-zones may use `--cream`.
- Nodes: flat pastel fill, no outline or stroke on nodes or zones, `--ink` title, `--ink-72` descriptor lines. No other text colors. Only arrows are stroked.
- Layers are told apart by pastel, not by new hues:

| Layer | Node fill |
|---|---|
| Frontend / UI | Blue `#ABE1EA` |
| Backend / core services (the system the doc is about) | Green `#B3E3C4` |
| Data engine / stores | Lavender `#E3D3FA` |
| External services, agents | Gold `#F8DB99` |
| Decision / risk / warning | Salmon `#F5B3AC` |

Rules:
- One pastel per layer, same pastel for that layer in every diagram. A node takes the pastel of the layer that owns it.
- Pastel on pastel is about 1.0 to 1.1 contrast, so never touch two pastel nodes: leave gaps and let white or cream show between them.
- Need more layers than five? Split the diagram. Never add a color.
- Allowed non-palette values: none. Retired: the old per-layer hues (`#1E78D6`, `#1B7A33`, `#5B3FD0`, `#0C8599`, `#C2680A`, `#C92A2A`, `#C2255C` and their tints).

### Arrows

- Color: `--ink`, 2px, open arrowheads.
- Direct one way: solid, single head. Direct two way: solid, heads both ends.
- Indirect (via another layer): dotted `--ink-soft`, single head, path stated in the label.
- Loops (rework, retries): orthogonal solid arrow, short label in the owning hue.
- Labels: small, ink, on the line, no box. Say what travels (`msgs / replies`, `jobs / status`), not "calls".
- Avoid crossing arrows. Reroute with elbows first.

### Legend

Any diagram with more than one arrow type starts with a top-left legend: solid single `Direct, one way`, solid double `Direct, two way`, dotted `Indirect (via <layers>)`.

### Layout and delivery

- Zones stack top to bottom: Frontend, Backend, AI Engine. Shared data layer gets a full-height column on the right.
- Entry node centered at top of its zone, children fan out below.
- Max about 25 nodes per diagram. Split and link past that.
- Build with the Excalidraw tool when available. Export SVG, keep the `.excalidraw` source in `docs/diagrams/`.
- Embed at full content width with a one-line meta-style caption.
- No Mermaid, PlantUML or draw.io default themes. If Mermaid is unavoidable, apply this palette via `themeVariables` and `classDef` and note it is a fallback.

## 6b. Presentations

- 16:9. `--cream` background with the dotted grid on every slide. Thin rounded frame optional.
- Every slide: eyebrow (section name, uppercase, `--ink-soft`) + bold sentence-style title ending with a period. Example: `ARCHITECTURE` / "One path between agents."
- Content lives in pastel cards (radius 10, `--shadow`, no border). One idea per card, max 3 cards for text, max 4 for stats.
- Cycle card colors: green, lavender, gold, salmon, blue. No two identical neighbors.
- Exactly one dark anchor per slide: a navy or ink pill or button-style element with cream text (key takeaway or next step).
- Type: title 44-56pt, card title 24-28pt, body 18-20pt minimum, meta 14pt. Nothing smaller.
- Max about 25 words per card. Fragments, no paragraphs.
- Title slide: eyebrow, huge headline, avatar stack of presenters, small meta, wordmark.
- Section dividers: full pastel background, headline only.
- Architecture slides: one diagram per slide in 6a style, full width, legend visible. Split big diagrams by zone.
- Roadmap / status slides: reuse the kanban look.
- Speaker notes carry the detail.
- No stock photos, clip art, gradients, emoji or bullet walls.
- Export PDF for sharing, keep source in `docs/decks/`.

## 6c. Logo

Files live in `flwn-design/logo/`. Use them as delivered. Never redraw, retype, recolor or rebuild.

### The logo

- The logo is the `flwn` wordmark only. No symbol, no tile, no lockup.
- Two versions: navy `#001F55` on light grounds, white `#FFFFFF` on navy.
- Outlined to paths, cropped tight. Never set it as live text.

| File | Use |
|---|---|
| `logo/flwn-wordmark-navy.svg` (+ `-1600.png`) | Cream, white, lavender, gold, green, salmon, blue |
| `logo/flwn-wordmark-white.svg` (+ `-1600.png`) | Navy only |

### Favicon

- The favicon is the gold circle mark: `#F8DB99` circle, navy `f`, navy dot lower right.
- Favicon, app icon and browser tab only. Never in a header, cover, slide or doc body.
- One drawing, shipped at the sizes each place needs. Each context renders at a different pixel size, so one raster would blur somewhere.

| File | Where |
|---|---|
| `logo/flwn-favicon-gold.svg` | Modern browser tabs, scales to any size |
| `logo/favicon.ico` (16, 32, 48 inside) | Legacy browsers, bookmarks, Windows |
| `logo/apple-touch-icon-180.png` | iOS home screen |
| `logo/pwa-192.png`, `logo/pwa-512.png` | Only if the app is a PWA (manifest) |

- Without a PWA, ship the first three only.
- Gold circle is faint on light tab backgrounds: 1.22:1 on cream, 1.35:1 on white. Navy `f` carries the shape. Never darken or outline the circle.
- The dot is about 1px at 16px and may blur.

### Contrast (computed)

- Navy wordmark: cream 14.3:1, white 15.8:1, lavender 11.3:1, gold 11.7:1, green 11.1:1, salmon 9.0:1, blue 11.1:1.
- White wordmark on navy 15.8:1. On any light ground 1.0 to 1.8:1, so never.
- Navy `f` on the gold circle 11.7:1.

### Placement

- Frontend: wordmark top-left of the app shell, sidebar header and login screen. Favicon in the browser tab.
- Docs: wordmark top-left of cover and README header (24-32px tall). Favicon for the docs site.
- Presentations: wordmark on the title slide and bottom-left of other slides.

### Size and space

- Min size: wordmark 64px wide.
- Clear space: at least the `l` stroke width on all sides.

### Don't

- Stretch, rotate, shadow, outline or add a stroke.
- Recolor the wordmark, or the favicon's circle, `f` or dot.
- Put the navy wordmark on navy or the white wordmark on a light ground.
- Add the circle mark, a tile, or a symbol next to the wordmark.
- Use the favicon as the main logo.
- Use the Blocks mark or any earlier logo.
- Put on busy photos, or in gradients.

## 7. Copy tone

- Plain, short, human. Sentence case everywhere except proper nouns.
- Status words: Backlog, Planned, In progress, Done.
- Metadata terse: `Aug 28`, `4 msg`, `Tomorrow`.

## 8. Rules

Do:
- Cycle pastel colors across cards. Avoid identical neighbors in a column.
- Keep one dark element per view as the anchor (diagram-free views: Meet button; AI avatar is the allowed second).
- Use CSS variables or Tailwind tokens. Never hardcode hex in components.
- Ink on pastel only. Navy on pastel is also allowed. Secondary text on pastel is `--ink-72`.

Don't:
- No colors outside section 2 (the logo uses only the colors listed in 6c).
- No red, orange or green status colors. Status = pastel squares, "Today" = white pill.
- No gradients, neon, pure black `#000`, or grey backgrounds.
- No heavy borders on cards. Color is the container.
- No shadow stacks beyond `--shadow`.
- No emoji in UI or docs.
- No white text on pastel.

## 9. Build checklist

1. Tokens come from section 2 or the Tailwind block below.
2. Headline bold, sentence-style, ends with a period.
3. Eyebrow present, uppercase, `--ink-soft`.
4. Cards: pastel fill, radius 10, `--shadow`, no border.
5. Dark elements limited to the Meet-style button and AI avatar.
6. Dotted cream canvas visible behind content.
7. Logo from `logo/`, unmodified. Wordmark only. Gold favicon only in the browser tab.
8. Mobile: columns collapse to one, side tabs become a bottom bar.
9. Logo circle is lavender. Icon tile is navy. Compare with the palette reference `palette-final.png`.

## 10. Tailwind starter

```js
// tailwind.config.js (extend)
colors: {
  cream: '#F7F3EA',
  white: '#FFFFFF',
  navy: '#001F55',
  ink: { DEFAULT: '#14131A', soft: '#6B6875' },
  pastel: {
    green: '#B3E3C4',
    lavender: '#E3D3FA',
    gold: '#F8DB99',
    salmon: '#F5B3AC',
    blue: '#ABE1EA',
  },
  line: '#E0DDD5',
  grid: '#D3CFC9',
},
borderRadius: { flwn: '10px' },
boxShadow: { flwn: '0 2px 0 rgba(20,19,26,.06), 0 8px 18px rgba(20,19,26,.08)' },
fontFamily: { sans: ['"Plus Jakarta Sans"', 'Inter', 'system-ui', 'sans-serif'] },
```

Usage: `bg-pastel-green`, `bg-pastel-lavender`, `bg-cream`, `text-ink`, `text-navy`, `text-ink-soft`, `border-line`, `rounded-flwn`, `shadow-flwn`. Secondary text on pastel: `text-ink/70`.
