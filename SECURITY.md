# Security policy

## Supported versions

Security fixes go into the latest release only: the newest version on the
[Releases](https://github.com/dagoSte/squadra/releases) page and the `main` branch. Please check that the problem is
still there in the latest release before you report it.

## Reporting a vulnerability

Report it privately, not in a public issue, discussion or pull request. Use GitHub private vulnerability reporting:

- open <https://github.com/dagoSte/squadra/security/advisories/new>, or
- in this repository, open the **Security** tab and choose **Report a vulnerability**.

Only you and the maintainer see the report.

## What to include

- The Squadra version (`squadra --version`) and how it is installed: Claude Code plugin, Codex plugin or standalone
  installer. Your operating system and Python version.
- What an attacker can do, and what they must control first: the registry folder, a project file, a dashboard plugin,
  a web page open in the same browser, a message from another window.
- The steps to reproduce it on a throwaway registry (`squadra init --temporary`), with the smallest input that shows it.
- No real secrets, no real registries and no other people's data.

## What to expect

The maintainer, Stefano D'Agostino, works on Squadra alone, so there is no fixed response time. You get your answers in
the private report. A confirmed problem is fixed in a new release; once that release is out, it is described in a
GitHub security advisory, with credit to you if you want it.

Problems in Claude Code, Codex, Cursor or other clients belong to their makers; report them there.

## Test fixtures are not secrets

Files under `tests/` contain synthetic credentials, such as `Synthetic-Only!Key-123`, `SENTINEL-SECRET-0f9e8d7c` and a
fake OpenSSH key header. They exist to prove that Squadra never shows, stores or sends a secret value, and they open
nothing. Secret scanners flag them; they are not leaked secrets.

Some JavaScript contract tests load the dashboard's script with `new Function` or `eval` inside the test harness. That
code runs only in the test suite; Squadra itself uses neither.
