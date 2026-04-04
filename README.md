# dlsite-opds

OPDS 1.2 server for [DLsite](https://www.dlsite.com/) Play purchases, with
[OPDS-PSE v1.2](https://anansi-project.github.io/docs/opds-pse/intro)
support for reading comics and manga page by page.

## Features

- Browse purchased DLsite works as a standard OPDS catalog
- **OPDS-PSE page streaming** -- read comics/manga page by page without downloading entire archives
- **Resized pages** -- clients can request a `{maxWidth}` for smaller screens
- **Reading progress** -- `pse:lastRead` / `pse:lastReadDate` attributes for restoring position
- **Descrambling** -- DLsite Play encrypted images are decoded automatically
- In-memory caching with background pre-fetching of page counts

## Quick Start (Docker)

The recommended way to run dlsite-opds is with Docker.

### Docker Compose (recommended)

Create a `docker-compose.yml`:

```yaml
services:
  dlsite-opds:
    image: ghcr.io/aokazenozomi/dlsite_opds:latest
    restart: unless-stopped
    ports:
      - "2580:2580"
    volumes:
      - dlsite-opds-data:/data

volumes:
  dlsite-opds-data:
```

Then start the server:

```bash
docker compose up -d
```

### Docker CLI

```bash
docker run -d \
  --name dlsite-opds \
  --restart unless-stopped \
  -v dlsite-opds-data:/data \
  -p 2580:2580 \
  ghcr.io/aokazenozomi/dlsite_opds:latest
```

### Connect your OPDS reader

Add the catalog in your OPDS reader, entering your DLsite credentials as
the username and password:

```
http://<host-ip>:2580/opds
Username: your_dlsite_login
Password: your_dlsite_password
```

Each OPDS client authenticates with its own DLsite account via HTTP
Basic Auth. Multiple readers can use different accounts simultaneously.

Page counts are resolved in the background after the first request;
PSE stream links will appear in the feed over the next few minutes.

## Configuration

Set as environment variables (or in a `.env` file for non-Docker installs).
In Docker, pass them with `-e` flags or under `environment:` in Compose.

| Variable | Default | Description |
|---|---|---|
| `DLSITE_OPDS_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in Docker) |
| `DLSITE_OPDS_PORT` | `2580` | Bind port |
| `DLSITE_OPDS_BASE_URL` | `http://{host}:{port}` | Public base URL (set when behind a reverse proxy) |
| `DLSITE_OPDS_DATA_DIR` | `~/.config/dlsite-opds` | Directory for progress and per-user data (`/data` in Docker) |
| `DLSITE_OPDS_CACHE_TTL` | `300` | Purchases cache TTL in seconds |
| `DLSITE_OPDS_PAGE_SIZE` | `30` | Entries per OPDS feed page |

To change the port in Docker, override both the variable and the port mapping:

```yaml
services:
  dlsite-opds:
    image: ghcr.io/aokazenozomi/dlsite_opds:latest
    environment:
      - DLSITE_OPDS_PORT=9090
    ports:
      - "9090:9090"
    volumes:
      - dlsite-opds-data:/data
```

DLsite credentials are not configured server-side. Each OPDS reader
provides its own DLsite username and password via HTTP Basic Auth.

## Running without Docker

Requires **Python 3.11+** and a DLsite account with purchased works.

1. **Install**

```bash
pip install -e .
```

2. **Start the server**

```bash
dlsite-opds
```

> You can also run `python -m dlsite_opds` or
> `uvicorn dlsite_opds.app:app --host 127.0.0.1 --port 2580` for more
> control.

## Endpoints

| Route | Description |
|---|---|
| `GET /opds` | Root navigation feed |
| `GET /opds/purchases?page=1` | Paginated purchases catalog |
| `GET /pse/{product_id}?page=0&width=800` | PSE page image (0-based index) |
| `GET /files/{product_id}/{file_hash}` | Raw file proxy (fallback) |
| `PUT /progress/{product_id}` | Update reading progress (`{"last_read": 10}`, 1-based) |
| `GET /progress/{product_id}` | Get reading progress |
| `GET /healthz` | Health check |

## OPDS-PSE

Implements the [OPDS Page Streaming Extension](http://vaemendis.net/opds-pse/)
v1.0 spec with [Anansi v1.2](https://anansi-project.github.io/docs/opds-pse/intro)
progression attributes.

- `{pageNumber}` is **0-based** (0 to N-1); `pse:lastRead` is **1-based** (1 to N)
- `{maxWidth}` is optional; pages wider than the value are scaled down
- All pages are served as `image/jpeg` (other formats converted server-side)
- Scrambled images are descrambled automatically

### Client Compatibility

| Client | PSE | Resize | Notes |
|---|---|---|---|
| [Panels](https://panels.app/) (iOS) | Yes | Yes | Primary target |
| [Chunky](http://chunkyreader.com/) (iOS) | Yes | Unknown | Listed as OPDS-PSE client |
| [KOReader](https://koreader.rocks/) | Partial | Unknown | May vary by version |
| Any OPDS 1.2 client | -- | -- | Covers + DLsite web links |

## Troubleshooting

| Problem | Fix |
|---|---|
| 401 on first request | Check username/password in your OPDS reader; verify at [dlsite.com](https://login.dlsite.com/login); social logins are unsupported |
| No PSE stream links | Page counts load in background -- wait a minute and refresh |
| 401 / session expired | Server re-authenticates automatically; check logs or restart if it persists |
| Can't access from LAN | Use `DLSITE_OPDS_HOST=0.0.0.0` (already set in Docker) and use your LAN IP |

## Limitations

- **Optimized files only** -- DLsite Play serves web-optimized versions (may differ from originals)
- **Image works only** -- PSE supports flat image ziptrees; ebook/video types are not yet supported
- **HTTPS recommended** -- credentials are sent via HTTP Basic Auth; use a reverse proxy with TLS when exposing to the network

## License

MIT -- see [LICENSE](LICENSE).

This software is for personal use. Users are responsible for compliance with
DLsite's Terms of Service.
