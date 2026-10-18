# Squadra README logo

`squadra-logo-light.svg` (for light pages) and `squadra-logo-dark.svg` (for dark pages) are the logo at the top of
`README.md` and `README.it.md`. The READMEs show them with `<picture>` and `prefers-color-scheme`. Each file is
self-contained: no font file, image or other resource is loaded.

- **Mark:** the listing icon described in [SQUADRA-ICON.md](SQUADRA-ICON.md), unchanged. Its glyph is the Bootstrap
  Icons 1.13.1 `grid-1x2` path, under the [Bootstrap Icons MIT licence](vendor/LICENSE-bootstrap-icons.txt). The dark
  variant adds a thin `#4a4541` edge to the badge so that it stays visible on a dark page.
- **Wordmark:** «Squadra» set in Geist, weight 600, from `vendor/fonts/geist-var-latin.woff2`, and converted to
  outlines. Geist is licensed under the [SIL Open Font License 1.1](vendor/fonts/OFL-geist.txt); the licence allows
  artwork made with the font, such as this wordmark, and the logo contains no font software.
- **Colours:** `#1f1c1b` and `#ff7714` in the mark; the wordmark is `#1f1c1b` on light pages and `#ebe9e1` on dark
  pages.

It is not the logo of a client or service provider. The manifests and the dashboard keep using `squadra-icon.*`.
