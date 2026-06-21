# AGENTS.md

## Cursor Cloud specific instructions

This repo is a single Python product: `dlsite-opds`, a FastAPI/Uvicorn OPDS 1.2 server
for DLsite Play purchases (page streaming via OPDS-PSE). There is no database, cache,
queue, or companion service — the app is one self-contained process. See `DEVELOPERS.md`
for the canonical run/test/endpoint reference and `.env.example` for config.

### Environment

- Python is used via a virtualenv at `.venv` (created during setup). Activate it with
  `. .venv/bin/activate` before running app/lint/test commands. The startup update script
  reinstalls deps into this venv.
- The native dependency `cykooz.resizer` builds from source and needs the Rust toolchain
  (`rustc`/`cargo`), which is already present on the image. If a wheel install fails with a
  Rust/cargo error, that toolchain is the cause.

### Run / lint / test (inside the venv)

- Run dev server: `uvicorn dlsite_opds.app:app --host 127.0.0.1 --port 2580 --reload`
  (equivalent: `dlsite-opds` or `python -m dlsite_opds`). Default bind is `127.0.0.1:2580`.
- Lint: `ruff check .`
- Tests: `pytest` (unit tests are fully mocked — no network/credentials needed).

### Non-obvious caveats

- The OPDS endpoints (`/opds`, `/pse/...`, etc.) require HTTP Basic Auth and authenticate
  **per-request against the live DLsite Play API** — there are no server-side credentials.
  With invalid/dummy creds, `/opds` returns `401 {"detail":"DLsite login failed"}` after a
  real upstream call. Unauthenticated requests return `401 {"detail":"Not authenticated"}`.
  `GET /healthz` returns `ok` and needs no auth — use it for quick liveness checks.
- Demonstrating real content (purchase catalog, page streaming) requires a valid DLsite
  account and internet access. The `e2e`-marked tests (`pytest -m e2e`) auto-skip unless
  `DLSITE_LOGIN_ID` and `DLSITE_PASSWORD` are set; without them ~14 tests are skipped.
- When credentials are set, login and catalog browsing work end-to-end (`/opds` and
  `/opds/purchases` return valid Atom feeds with real entries). However, the page-image
  download/streaming `e2e` tests currently fail for every work with a crypt-image
  dimension mismatch (e.g. `got 1024x1280, expected 907x1280`) — the PSE descramble/
  validation path, not the environment. Expect ~7 e2e failures of this kind; they are
  independent of dependency setup. Several e2e tests also hardcode product IDs (e.g.
  `RJ01459324`) that only exist in specific accounts.
- Docker/Compose and the `infra/digitalocean` Caddy assets are for production deployment
  only and are not needed for local dev or testing.
