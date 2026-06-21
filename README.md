# dlsite-opds

Read your [DLsite](https://www.dlsite.com/) Play manga and comics in
[Panels](https://panels.app/) (or any OPDS reader) — page by page, without
downloading whole archives.

Uses [OPDS 1.2](https://specs.opds.io/opds-1.2) and
[OPDS-PSE v1.2](https://anansi-project.github.io/docs/opds-pse/intro) for
streaming.

## Features

- Browse purchased DLsite works as an OPDS catalog
- **Page streaming** — read comics/manga one page at a time
- **Resized pages** — smaller screens can request a narrower width
- **Reading progress** — pick up where you left off
- **Descrambling** — DLsite Play encrypted images decoded automatically
- **Multi-chapter works** — folder/PDF chapters with per-chapter streaming

## Panels

[Panels](https://panels.app/) (iOS/iPadOS/macOS) is the primary target client.

1. **Library** → **⋯** → **Connect Service** → **OPDS**
2. Fill in the fields (prod or nightly — same server, different slot):

| Field | Prod | Nightly |
| --- | --- | --- |
| **Alias** | any name (e.g. `DLsite`) | any name (e.g. `DLsite nightly`) |
| **Host** | `dlsite-opds.aokazenozomi.com` | `dlsite-opds-nightly.aokazenozomi.com` |
| **Port** | `2580` | `2581` |
| **Username** | your DLsite login | your DLsite login |
| **Password** | your DLsite password | your DLsite password |

**Prod** tracks the stable release; **nightly** tracks the latest `main` build
(may be less stable). Use separate Panels entries if you want both.

Use your DLsite email/username — not Google/Twitter/social login.

3. Tap **Apply**

The server appears as a library and an import service:

- **Stream:** open the library → **Purchases** or a category → tap a title to read
- **Download:** use the import service to copy titles into your on-device library

Do not include `https://` or `/opds` in **Host**; enter the domain only.

## Other OPDS readers

| | Prod | Nightly |
| --- | --- | --- |
| Catalog URL | `https://dlsite-opds.aokazenozomi.com:2580/opds` | `https://dlsite-opds-nightly.aokazenozomi.com:2581/opds` |

Use your DLsite login as username and password. PSE streaming works best in
Panels and Chunky; other clients may show covers and web links only.

## Limitations

- DLsite Play optimized files only (may differ from original downloads)
- Image archives only — ebooks and video are not supported yet
- Personal use; comply with [DLsite Terms of Service](https://www.dlsite.com/home/user/regulations)

## Self-hosting

Run your own server with Docker or Python. Deployment, configuration, API
reference, and development setup: **[DEVELOPERS.md](DEVELOPERS.md)**.

DigitalOcean CI/CD with Caddy TLS: **[INFRA.md](INFRA.md)**.

Promote nightly to prod: **[create PR (`main` → `prod`)](https://github.com/AokazeNozomi/dlsite_opds/compare/prod...main?expand=1)**.

## License

MIT — see [LICENSE](LICENSE).
