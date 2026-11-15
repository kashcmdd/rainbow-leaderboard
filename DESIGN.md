# DESIGN.md

## World

**Tactical ops / match dossier.** After-action reports, ladder printouts and
briefing boards: gunmetal and paper, hairline rules, condensed headings,
monospaced figures, one signal colour. No glow, no gradient decoration, no
emoji reactions.

Dark is the default; light is a warm paper counterpart with the same structure.

## Tokens

All colour comes from CSS custom properties on `:root[data-theme]`. Components
and canvases read the same names.

| Token | Dark | Light |
|---|---|---|
| `--bg` | `#0b0d0e` | `#efede8` |
| `--panel` | `#101315` | `#fbfaf7` |
| `--panel-2` | `#151a1d` | `#f4f2ec` |
| `--panel-3` | `#1a2024` | `#eae7df` |
| `--line` | `rgba(255,255,255,.075)` | `rgba(20,23,26,.12)` |
| `--line-strong` | `rgba(255,255,255,.16)` | `rgba(20,23,26,.26)` |
| `--ink` | `#e9edee` | `#15181a` |
| `--ink-2` | `#a3adb1` | `#4d565b` |
| `--ink-3` | `#7b868b` | `#626c70` |
| `--signal` | `#e8197d` | `#c9126b` |
| `--signal-ink` | `#f45aa3` | `#c9126b` |
| `--win` | `#59d38c` | `#1f8a4d` |
| `--loss` | `#e86767` | `#b93a3a` |
| `--warn` | `#e3b341` | `#8a5c00` |

Rank colours are data (`app/ranks.py`) and arrive per row as `--rank-color`.
Text and strokes use `--rank-ink`, derived per theme so gold and silver stay
legible on paper:

- dark: the raw rank colour
- light: `color-mix(in srgb, var(--rank-color) 55%, #000)`

## Type

- **Display** — Barlow Condensed 500/600, uppercase, tight tracking. Page
  titles, section titles, podium numerals, wordmark.
- **Body** — IBM Plex Sans 400/500/600. Running text, buttons, forms, tables.
- **Data** — JetBrains Mono 400/500/600 with tabular figures. ELO, deltas,
  counts, timestamps, labels, table headers.

Labels are 10px mono uppercase with `0.14em` tracking. Body copy is 13–14px.
Page titles are 34–40px; the landing title is 46–64px.

## Layout

- `shell`: max 1180px, 16px gutters (24px from `sm`).
- 3px corner radius; 1px hairlines; no shadows.
- Vertical rhythm in 4/8px steps. Panels are separated by 12–16px.
- Page header: eyebrow, display title, one micro metadata line, actions right.
- Data tables: sticky header, 10px/12px cell padding, right-aligned numerals.
- Faint two-axis 48px grid on the page background; it belongs to the dossier
  measurement surface and stays at ~2% opacity.

## Components

Defined once in `app/static/css/input.css` (`@layer components`) and reused:

- `.panel`, `.panel-2`, `.card` (legacy alias for admin).
- `.data-table` with `.text-right` header support.
- `.seg` / `.seg-item` (formats, tabs).
- `.badge` + `-signal`, `-win`, `-loss`, `-warn`, legacy status variants.
- `.rank-chip`, `.rank-elo`.
- `.stat-label`, `.stat-value`, `.meter`, `.micro`, `.num`, `.display`, `.eyebrow`.
- `.field`, `.field-label`, `.btn` + `-pink`, `-danger`, `-sm`, `-ghost`.
- `.nav-link`, `.nav-stack`, `.theme-toggle`.
- `.skeleton` (opacity pulse), `.alert` (`-error`, `-success`).
- `.bracket-round`, `.bracket-match`, `.tbd`.
- `.tabbar` / `.tab-item` / `.sheet` / `.sheet-link` — mobile bottom navigation
  and the “More” sheet. Visible below `md`; body reserves
  `58px + env(safe-area-inset-bottom)`.
- Form ticks (five bordered W/L squares) and the movement label (`+24 7d`).
- Rating deviation: `±RD` under the rating and a `Provisional` badge while
  RD is above the configured threshold.
- Season selector in the leaderboard toolbar; archived seasons show a signal
  note and hide the Teams tab.
- Match/team dossiers reuse the same panel, badge, meter and rank-chip pieces.
- Report page: two-step flow with an ELO preview panel and duplicate warning.
- Mobile patterns: 3-up stat strip on home, 3-up compact podium (rank, avatar,
  name, ELO, movement), collapsed match filters, single-row match scoreboard,
  tournament Bracket/Participants tabs, and no roster panel for 1v1 teams.

Templates must not carry raw hex colours. Page-specific `<style>` blocks are
reserved for layout quirks, not colour.

## Theme

- `data-theme="dark|light"` on `<html>`; `color-scheme` follows.
- Inline head script runs before paint: `localStorage.theme` → system
  preference → dark. `?theme=dark|light` forces a theme for sharing/screenshots.
- The toggle dispatches `themechange`; the ELO canvas redraws from CSS vars.
- No flash of the wrong theme.

## Motion

- Entrance: 280ms `riseIn` (opacity + 6px), staggered in 30ms steps.
- Bars: width transitions on load only.
- Skeletons: 1.5s opacity pulse.
- Everything collapses under `prefers-reduced-motion: reduce`.
- No infinite glow, spin, or marquee.

## Accessibility

- `:focus-visible` 2px signal outline, offset 2px.
- Contrast checked for both themes; pink text uses `--signal-ink` on tinted
  surfaces.
- Tables keep semantic `thead`/`tbody`; canvases have adjacent text summaries.
- Theme and menu toggles expose `aria-expanded` / labels; Escape closes the
  mobile menu; a skip link precedes the header.
- Interactive rows are real links; the search input is labelled.

## Build

Tailwind 3.4 with custom colour names mapped to the tokens:

```sh
npm install
npm run tw:build   # app/static/css/tailwind.min.css (committed)
npm run tw:watch   # during development
```

`node_modules` is ignored by git and Docker; the built CSS is what ships.

`app/static/og.png` is a rendered 1200×630 card. Regenerate it when the site
name changes (`/tmp` helper or any HTML renderer at 1200×630).
