# Legend — marketing site

The public site for **Legend**, published to GitHub Pages at
**https://samratrajsharma.github.io/legend/**.

It ships two pages:

| Page | Source | Served at | Type |
|------|--------|-----------|------|
| **Landing (homepage)** | `public/v3.html` | `/` (published as `index.html`) | Hand-authored, self-contained static HTML |
| **Docs** | `docs.html` + `src/docs-main.tsx`, `Docs.tsx` | `/docs.html` | React (built by Vite) |

The landing page is a single standalone file — its own dark + green theme, inline
CSS/JS, no framework — so what you write in `public/v3.html` is exactly what ships.
The docs page is a small React app built by Vite.

## Deploy

Pushing anything under `legend-frontend/**` to `main` triggers
[`.github/workflows/pages.yml`](../.github/workflows/pages.yml), which:

1. runs `npm ci && npm run build` (builds `docs.html` and copies `public/*` into `dist/`), then
2. copies `public/v3.html` over `dist/index.html` so the **v3 page is the homepage**, then
3. deploys `dist/` to GitHub Pages.

One-time setup (already done for this repo): **Settings → Pages → Build and
deployment → Source: "GitHub Actions"**.

## Change the landing page

Edit **`public/v3.html`** — that's the live homepage. To preview it, just open the
file in a browser (it needs no build). Commit and push to `main` and Pages redeploys
automatically. `public/v2.html` is the previous version, kept for reference; to roll
back, copy it over `v3.html` (or point the workflow's copy step at it).

## Run / build locally

```bash
npm install
npm run dev        # http://localhost:5300 — dev server (docs app + old React landing)
npm run build      # -> dist/   (tsc -b && vite build)
npm run preview    # serve the built dist/ locally
```

> Note: `npm run dev` serves the Vite entry (`index.html`, the old React landing) and
> `docs.html`. The published homepage is the static `public/v3.html` — preview that by
> opening the file directly, or by running `npm run build` (the workflow's copy step
> mirrors that) and `npm run preview`.

## Structure

```
public/
  v3.html            the live landing page (static) — edit this
  v2.html            previous landing version (reference)
  legend-icon.svg    favicon, copied to the site root
docs.html            docs page entry (React, built by Vite)
index.html           old React landing entry (still builds; superseded by v3 on the live site)
vite.config.ts       multi-page build: index.html + docs.html; base './'
src/
  docs-main.tsx      docs entry — renders the docs page
  Docs.tsx / .css    the docs page
  main.tsx           old React landing entry
  Legend.tsx / .css  the old React landing
  theme.css          design tokens (dark + green), base styles
  Navbar/Footer      shared React nav/footer (used by the React pages)
  links.ts           DOCS_URL / GITHUB_URL / PYPI_URL for the React pages
```
