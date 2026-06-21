# Developer guide

Self-hosting, deployment, and contributing to dlsite-opds.

For end-user setup in [Panels](https://panels.app/), see [README.md](README.md).

## Quick start (Docker)

```yaml
# docker-compose.yml
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

```bash
docker compose up -d
```

Or with the CLI:

```bash
docker run -d \
  --name dlsite-opds \
  --restart unless-stopped \
  -v dlsite-opds-data:/data \
  -p 2580:2580 \
  ghcr.io/aokazenozomi/dlsite_opds:latest
```

### Connect an OPDS reader

Add the catalog in your OPDS reader. Use your DLsite credentials as the
username and password.

**Panels** uses separate Host/Port fields — see [README.md](README.md#panels).

Other clients often take a single catalog URL:

```text
http://<host-ip>:2580/opds
Username: your_dlsite_login
Password: your_dlsite_password
```

Each client authenticates with its own DLsite account via HTTP Basic Auth.
Multiple readers can use different accounts simultaneously.

Page counts resolve in the background after the first request; PSE stream
links appear in the feed over the next few minutes.

## Configuration

Set as environment variables (or in a `.env` file for non-Docker installs).
In Docker, pass them with `-e` flags or under `environment:` in Compose.

| Variable | Default | Description |
|---|---|---|
| `DLSITE_OPDS_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in Docker) |
| `DLSITE_OPDS_PORT` | `2580` | Bind port |
| `DLSITE_OPDS_BASE_URL` | `http://{host}:{port}` | Public base URL (set when behind a reverse proxy) |
| `DLSITE_OPDS_DATA_DIR` | `~/.config/dlsite-opds` | Progress and per-user data (`/data` in Docker) |
| `DLSITE_OPDS_CACHE_TTL` | `300` | Purchases cache TTL in seconds |
| `DLSITE_OPDS_PAGE_SIZE` | `30` | Entries per OPDS feed page |
| `DLSITE_OPDS_IMAGE_CACHE_TTL` | `86400` | Page image cache TTL in seconds |
| `DLSITE_OPDS_PREFETCH_AHEAD` | `5` | Pages to prefetch ahead of the current read position |

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

DLsite credentials are not configured server-side. Each OPDS reader provides
its own username and password via HTTP Basic Auth.

See [`.env.example`](.env.example) for a template.

## Running without Docker

Requires **Python 3.11+**.

```bash
pip install -e .
dlsite-opds
```

Alternatives:

```bash
python -m dlsite_opds
uvicorn dlsite_opds.app:app --host 127.0.0.1 --port 2580
```

## Deployment (DigitalOcean)

GitHub Actions deploys nightly (`main`) and prod (`prod`) on one droplet with
shared Caddy TLS on ports `2580` / `2581`. Full setup: [INFRA.md](INFRA.md).

```text
https://<your-domain>:2580/opds    # prod
https://<your-domain>:2581/opds    # nightly
```

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

### Client compatibility

| Client | PSE | Resize | Notes |
|---|---|---|---|
| [Panels](https://panels.app/) (iOS) | Yes | Yes | [User setup](README.md#panels) |
| [Chunky](http://chunkyreader.com/) (iOS) | Yes | Unknown | Listed as OPDS-PSE client |
| [KOReader](https://koreader.rocks/) | Partial | Unknown | May vary by version |
| Any OPDS 1.2 client | -- | -- | Covers + DLsite web links |

## Troubleshooting

| Problem | Fix |
|---|---|
| 401 on first request | Check username/password in the OPDS reader; verify at [dlsite.com](https://login.dlsite.com/login); social logins are unsupported |
| No PSE stream links | Page counts load in background — wait a minute and refresh |
| 401 / session expired | Server re-authenticates automatically; check logs or restart if it persists |
| Can't access from LAN | Use `DLSITE_OPDS_HOST=0.0.0.0` (already set in Docker) and your LAN IP |

## Development

```bash
pip install -e ".[test]"
pytest
```

E2E tests that hit live DLsite Play require credentials and are marked `e2e`:

```bash
pytest -m e2e   # optional; needs DLSITE_* env vars
```

Project layout:

```text
dlsite_opds/
  app.py           FastAPI app
  routes/          OPDS, PSE, progress, covers
  services/        feeds, CBZ, image cache, prefetch
  core/            auth, config, DLsite client
infra/digitalocean/  Caddy + cloud-init for DO deploy
tests/
```

Pull requests welcome. Run `pytest` before submitting.

## Limitations

- **Optimized files only** — DLsite Play serves web-optimized versions (may differ from originals)
- **Image works only** — PSE supports flat image ziptrees; ebook/video types are not yet supported
- **HTTPS recommended** — credentials are sent via HTTP Basic Auth; use TLS when exposing to the network
