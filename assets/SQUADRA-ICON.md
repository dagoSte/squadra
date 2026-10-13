# Squadra listing icon

`squadra-icon.png` is the existing dashboard mark exported at 512 × 512 pixels.
`squadra-icon.svg` is its vector source. It uses Squadra's dashboard colours
and the same Bootstrap Icons 1.13.1 `grid-1x2` paths already used in that mark;
the [Bootstrap Icons MIT licence](vendor/LICENSE-bootstrap-icons.txt) applies
to those paths. It is not the logo of a client or service provider.

The Claude directory's [plugin checklist](https://claude.com/docs/plugins/pre-submission-checklist)
accepts complete PNG images, and the [manifest reference](https://code.claude.com/docs/en/plugins/manifest-reference#directory-listing-fields)
requires an icon path inside the plugin. Both pages were consulted on 13 October
2026; they do not state a minimum pixel size. The bundled PNG is 512 × 512 and
13,257 bytes, below the checklist's 5 MiB per-file limit. Both client manifests
refer to this local asset; no remote image is needed to render it.
