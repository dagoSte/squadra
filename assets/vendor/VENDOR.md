# Vendored third-party parts

The dashboard loads nothing from the network. The Bootstrap files are copied here byte for byte from the official npm
packages and the page carries them inline; the two typefaces are copied byte for byte from memoranda.dev and the dashboard
serves them itself. `tests/test_panel.py` checks that the page's first style block equals
the Reboot file, that every icon in the page equals its SVG file path for path, and that every hash below matches.

| Package | Version | Published | Licence | Tarball | npm integrity |
|---|---|---|---|---|---|
| Bootstrap | 5.3.8 | 2025-08-26 | MIT | https://registry.npmjs.org/bootstrap/-/bootstrap-5.3.8.tgz | `sha512-HP1SZDqaLDPwsNiqRqi5NcP0SSXciX2s9E+RyqJIIqGo+vJeN5AJVM98CXmW/Wux0nQ5L7jeWUdplCEf0Ee+tg==` |
| Bootstrap Icons | 1.13.1 | 2025-05-09 | MIT | https://registry.npmjs.org/bootstrap-icons/-/bootstrap-icons-1.13.1.tgz | `sha512-ijombt4v6bv5CLeXvRWKy7CuM3TRTuPEuGaGKvTV5cz65rQSY8RQ2JcHt6b90cBBAC7s8fsf2EkQDldzCoXUjw==` |

Only these parts are used:

- `dist/css/bootstrap-reboot.min.css`, the base stylesheet: it has no class selectors and no root rule the page's
  tests read. The full `bootstrap.min.css` is not used: its grid `.row` class would restyle the page's own `.row`
  items, its top-level `:root` breakpoint rule fails the light-theme test, and it would add 232,111 bytes to the page.
- 40 icons from `icons/`, each made of `<path>` elements only. Bootstrap Icons 1.13.2 was not taken: it was published
  on 2026-10-08, two days before this copy.
- No Bootstrap JavaScript: its tooltip and popover template code writes `innerHTML`, which the page forbids.

## Typefaces (memoranda.dev)

The dashboard uses the typefaces of memoranda.dev: Geist for all text and Fraunces for the display headings only. Both
files are the site's own subsets, `website/assets/fonts/` in the memoranda-hosted repository, copied without any change
(sha256 below, equal to the coordinator's `tasks/brand-sources.sha256`). They are not on the page: `squadra dash` answers
`/fonts/geist-var-latin.woff2` and `/fonts/fraunces-var-latin.woff2` from this folder, and the page's policy allows fonts
from the dashboard itself only (`font-src 'self'`). If a file cannot load, the page falls back to the system fonts.

| Typeface | File | Version (name table) | Axes | Licence |
|---|---|---|---|---|
| Geist | `fonts/geist-var-latin.woff2` | Version 1.800 | wght 100–900 | SIL Open Font License 1.1, `fonts/OFL-geist.txt` |
| Fraunces | `fonts/fraunces-var-latin.woff2` | Version 1.000;[b76b70a41] | opsz 9–144, wght 100–900 | SIL Open Font License 1.1, `fonts/OFL-fraunces.txt` |

Each licence file starts with the copyright line and the licence address stored in the font's own name table, followed
by the unchanged text of the SIL Open Font License 1.1.

| File | sha256 |
|---|---|
| `LICENSE-bootstrap-icons.txt` | 0fb3e11bd57e896c5a512afd64864d28a37de45d19835016c87ca1ad19ead969 |
| `LICENSE-bootstrap.txt` | 4620c84ad5ce8602ff65640ed6b7c8b78ebb9e036584f0ebc1ccc88206a4bb51 |
| `bootstrap-icons-1.13.1/archive.svg` | 6d487e2c88276f6c88de13e3b725a1c59d772f86d7b881701a40f01b275ae46a |
| `bootstrap-icons-1.13.1/arrow-down-up.svg` | 6aaf945a768d74eeb41076c397732562b568b466b789a918e3e07b9b76d0a317 |
| `bootstrap-icons-1.13.1/arrow-down.svg` | 9d2ae06a1b4f766591450808857c72b6935345e4b2d8e36d64c31c4d5003cd9b |
| `bootstrap-icons-1.13.1/arrow-up.svg` | b6d86790781ac475d20f54ead8284e26baadd2fb867250455cf7aa7030bd7ce3 |
| `bootstrap-icons-1.13.1/bar-chart.svg` | 7c96b89a576ef1e955d9b503274b62566f128a1e5148d65435eeaf9016fe91be |
| `bootstrap-icons-1.13.1/broadcast.svg` | 241b1603f246ad012c98e743617318566f9cc16a5a2829d4692ac55ae84e4a69 |
| `bootstrap-icons-1.13.1/chat-dots.svg` | c0584ea243f0341af3bc7efe9a4c965adc55e0f294ea4d331e00753f9db34a76 |
| `bootstrap-icons-1.13.1/check-circle.svg` | ddad2cb113b8e3c21deea3ae876e7790982b246df345f8f03451218e1ea3a711 |
| `bootstrap-icons-1.13.1/check2-all.svg` | c61cb1382473e7dc85231f2feddae31cdf8baf2c4be503c690274c1a627558ea |
| `bootstrap-icons-1.13.1/chevron-double-left.svg` | bbae4eed371b11ae1880a67a3443e1f3bff20627bc0b164056092febe74067e5 |
| `bootstrap-icons-1.13.1/chevron-double-right.svg` | 4037fa65a7f0fbbbdac963df95021ab05c7c31962d3a9f646a7fded1220a2f81 |
| `bootstrap-icons-1.13.1/chevron-down.svg` | 6c98af73d9cd64d0f4ae890c40566a436a1cc904af93c283e52111af2d4ba44c |
| `bootstrap-icons-1.13.1/chevron-right.svg` | 51b9c4168b12b378acde3131234ffe2b2fa5ed3c80a944a7dc864bb515b469cf |
| `bootstrap-icons-1.13.1/circle-half.svg` | 65de347262cd8a8a1e852e7af6008b10e45f417b61db00dd54e51e5fd13ccdd5 |
| `bootstrap-icons-1.13.1/clipboard-check.svg` | a9875a9254fb2f3ddec2d33a8c91699660174e7328ad00c4b067e822a1b87431 |
| `bootstrap-icons-1.13.1/clock-history.svg` | 4e379874f5bc65478455eea51815638c24a0041b087149ffbc7d243535d4d829 |
| `bootstrap-icons-1.13.1/dash-circle.svg` | dbcef17e27bd4107fa1f6e80b4dc757ad770cb75997f1b9c735ec70f4d0e0476 |
| `bootstrap-icons-1.13.1/diagram-3.svg` | d48107b77d139e2c6f71354a3a0bb9b9a9314c51e2b606932082ee51d580a9ae |
| `bootstrap-icons-1.13.1/envelope-exclamation.svg` | e4c916cac336a9e7aaa4bf43241a22de001431d02143044e2c5ff6fdbe79baa9 |
| `bootstrap-icons-1.13.1/envelope.svg` | 46354ede34e6acffd9828377884007ae3090db3b892e4b4a8fb8eec3e1017d63 |
| `bootstrap-icons-1.13.1/exclamation-triangle.svg` | acf2f83808131b92632e6d57aad39c02cd6e3ade54fcfdead346dc7af22a6a47 |
| `bootstrap-icons-1.13.1/grid-1x2.svg` | 5ab371c57d3fc9d7fdb0662f49e39bd060dc66f8e5c4da4de6043b74226dd472 |
| `bootstrap-icons-1.13.1/hourglass-split.svg` | 7c2c5e404704cc4689cef2f69bb353bc48d55ca2901a34616e908e1a378c5fcf |
| `bootstrap-icons-1.13.1/info-circle.svg` | 94836458bf63431ff37c41dedc6818270fd7ec72a9fb2da917ef2d00968f97ca |
| `bootstrap-icons-1.13.1/journal-text.svg` | 72311fb829c2499196797bc4212bc36cc030e1ab9a42e327cc79475383a8bcc0 |
| `bootstrap-icons-1.13.1/layout-sidebar-inset.svg` | 58d68080f53e2b99289f458234c1335d74e136a04631c0a1a7772d868fdecc6f |
| `bootstrap-icons-1.13.1/list-check.svg` | cb5883331845d5fc9a75a93dd489b519e2ec2d9077a04e2150d73d79d2d6ed22 |
| `bootstrap-icons-1.13.1/list.svg` | ca32e9e89788d0721410969df4fe0e0327db886ee7460f018e615a85433d987e |
| `bootstrap-icons-1.13.1/moon-stars.svg` | a0f0b6e4ff0a84c4375716afc0975bf8a518956676eeabc485b5092f93e2b858 |
| `bootstrap-icons-1.13.1/pause-circle.svg` | c240a16fb9eeb6bf41f2473395d51cd75bc892332295dd03edcc68f7d130f158 |
| `bootstrap-icons-1.13.1/people.svg` | f2207fe57c114d0d80b4eb5b4986ddce211fbe806b26b89a95a59723ac9d9b9d |
| `bootstrap-icons-1.13.1/search.svg` | dd6f471f48724227dfde72d6253a98023d4b734d06b90a3d932cf698e3aaa1c9 |
| `bootstrap-icons-1.13.1/speedometer2.svg` | aa49f93d592c5938b9395ea8bae7a56d313f4333634daa9d9b4fc6428546f73a |
| `bootstrap-icons-1.13.1/sun.svg` | 72be1ffbedea58a5cf68b136627439b42c26643765f230fc807496998ed2141d |
| `bootstrap-icons-1.13.1/table.svg` | 16f4839707f49e01d2079460b6519185037db6283b4bcd41b4b270b56944d299 |
| `bootstrap-icons-1.13.1/translate.svg` | 9a86eae6065e3db9e0bfea91803d4258e0ddc50fcb69797f13fb3cf856967097 |
| `bootstrap-icons-1.13.1/wifi-off.svg` | 1c3990b804510c490194e0666d232596bcdbb1d18bdbceb03173adfae24cf406 |
| `bootstrap-icons-1.13.1/window-stack.svg` | 456858443ec31ab38487e1cb57395ab0f6f8ecd3182d10f5530e47f381f2bb62 |
| `bootstrap-icons-1.13.1/x-circle.svg` | 39d34f09f6433c903a675ec5e4fc34f693bfba833354f052152aa0958354f6f5 |
| `bootstrap-icons-1.13.1/x-lg.svg` | 5ac6965c9989afaad195dc0fe739bd7443a7e94e52f8530b9b7d664d20c5d9e5 |
| `bootstrap-reboot-5.3.8.min.css` | 0b56cde507cfa4d5d219f71df2e2d0a9b14bd6f589583818b874fb026f0f47f7 |
| `fonts/OFL-fraunces.txt` | 8375058d603db35b2ac0dbc3df2f3565c9d0a4f93adf1161daea43e9294b10c9 |
| `fonts/OFL-geist.txt` | c683bfbcc7e087f5d37a54ef628f10387c451a83ddc459b151403a164ac46c90 |
| `fonts/fraunces-var-latin.woff2` | 48282a415ec22e31beaf0a0666e6fae0c8cbddcd0b1f6e729f27c3ade8a64e43 |
| `fonts/geist-var-latin.woff2` | 9b6f5ff45b278c744b5f379a2c4ecbaf858a842b8eaf82ac8d21b699ca16c608 |
