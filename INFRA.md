# Infrastructure Setup

One DigitalOcean droplet runs nightly and prod side-by-side. Shared Caddy
terminates TLS: OPDS on `:2580` / `:2581` (prod / nightly); `:443` reserved
for a website. `main` → nightly slot; `main → prod` PR → prod slot.

GitHub env names stay `dev` / `dev-gate`; on-droplet naming uses **nightly**.

| GitHub env | Branch | APP_PATH | Container | Domain (example) |
| --- | --- | --- | --- | --- |
| `dev` | `main` | `/opt/dlsite-opds-nightly` | `dlsite-opds-nightly` | `opds-nightly.example.com` |
| `prod` | `prod` | `/opt/dlsite-opds` | `dlsite-opds` | `opds.example.com` |

Caddy: `/opt/dlsite-opds-caddy`. Apps use Docker network `opds_shared`.

- `dev-gate` / `prod-gate` — required reviewers
- `dev` / `prod` — deploy targets (no reviewers)
- DO droplet vars — repository level only

Before first deploy: create `dev-gate` and `dev`. For prod, see
[Prod promotion](#prod-promotion).

## Example values

### Repository secrets

| Secret | Example value |
| --- | --- |
| `DIGITALOCEAN_TOKEN` | `dop_v1_a1b2c3d4e5f6789012345678901234567890abcdef0123456789abcdef` |
| `SSH_DEPLOY_PRIVATE_KEY` | Full contents of `dlsite-opds-deploy` (no passphrase; trailing newline required) |
| `SSH_DEPLOY_PUBLIC_KEY` | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGexamplekeycomment dlsite-opds-deploy` |
| `SSH_HOST_PRIVATE_KEY` | Full contents of `dlsite-opds-host` (no passphrase; trailing newline required) |
| `SSH_HOST_PUBLIC_KEY` | `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHexamplehostkey dlsite-opds-host` |
| `GHCR_PULL_TOKEN` (optional) | `ghp_1234567890abcdefghijklmnopqrstuvwxyz12` |

```text
# SSH_DEPLOY_PRIVATE_KEY / SSH_HOST_PRIVATE_KEY — shape only; use your generated keys
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
...
-----END OPENSSH PRIVATE KEY-----

```

### Repository variables

| Variable | Example |
| --- | --- |
| `DO_REGION` | `nyc3` |
| `DO_SIZE` | `s-1vcpu-1gb` |
| `DO_IMAGE` | `ubuntu-24-04-x64` |
| `DO_DROPLET_NAME` | `dlsite-opds` |
| `DO_ENABLE_BACKUPS` | `false` |
| `DO_SWAP_SIZE` | `1G` |
| `DO_PROJECT_NAME` | `DLsite OPDS` |
| `DO_PROJECT_PURPOSE` | `Service or API` |
| `DO_PROJECT_ENVIRONMENT` | `Production` |

### `dev` environment (nightly)

| Variable | Example |
| --- | --- |
| `OPDS_DOMAIN` | `opds-nightly.example.com` |
| `DLSITE_OPDS_BASE_URL` | `https://opds-nightly.example.com:2581` |
| `APP_PATH` | `/opt/dlsite-opds-nightly` |

Deploy writes `OPDS_EXTERNAL_PORT=2581` into the slot `.env`. Caddy listens
on `OPDS_DOMAIN:2581` and reverse-proxies to the app container on port 2580.

### `prod` environment

| Variable | Example |
| --- | --- |
| `OPDS_DOMAIN` | `opds.example.com` |
| `DLSITE_OPDS_BASE_URL` | `https://opds.example.com:2580` |
| `APP_PATH` | `/opt/dlsite-opds` |

Deploy writes `OPDS_EXTERNAL_PORT=2580` into the slot `.env`. Caddy listens
on `OPDS_DOMAIN:2580` and reverse-proxies to the app container on port 2580.

### DNS (both → same droplet IP)

```text
opds-nightly.example.com.  300  IN  A  203.0.113.10
opds.example.com.          300  IN  A  203.0.113.10
```

Replace `203.0.113.10` with the droplet IP from the workflow `discover` job.

## 1. DigitalOcean token

Create a token with these custom scopes:

- `droplet:read`, `droplet:create`
- `ssh_key:read`, `ssh_key:create`
- `firewall:read`, `firewall:create`, `firewall:update`
- `tag:read`, `tag:create`
- `project:read`, `project:create`, `project:update`

Repository secret:

```text
DIGITALOCEAN_TOKEN
```

## 2. Deploy SSH key

No passphrase — GitHub Actions uses non-interactive SSH (`BatchMode=yes`).

```bash
ssh-keygen -t ed25519 -C "dlsite-opds-deploy" -f dlsite-opds-deploy -N ""
```

Repository secrets: `SSH_DEPLOY_PRIVATE_KEY`, `SSH_DEPLOY_PUBLIC_KEY`

The deploy key must match in three places (same keypair):

1. GitHub secrets (`SSH_DEPLOY_PRIVATE_KEY` + `SSH_DEPLOY_PUBLIC_KEY`)
2. DigitalOcean account SSH key `dlsite-opds-deploy` (imported on first provision)
3. Droplet `deploy` user `authorized_keys` (baked by cloud-init at **first boot only**)

Verify private/public secrets match each other:

```bash
ssh-keygen -y -f dlsite-opds-deploy
# must match SSH_DEPLOY_PUBLIC_KEY / dlsite-opds-deploy.pub
```

Verify DO account key matches GitHub public key:

```bash
doctl compute ssh-key list --format Name,FingerPrint
ssh-keygen -E md5 -lf dlsite-opds-deploy.pub
# FingerPrint column must match MD5 line (without "MD5:" prefix)
```

If you rotate this keypair: update GitHub secrets, delete the droplet **and** DO
SSH key `dlsite-opds-deploy`, re-run workflow. Droplet-only delete is not
enough — provision fails with fingerprint mismatch while the old DO key exists.

```bash
doctl compute droplet delete <droplet-id> --force
doctl compute ssh-key delete <ssh-key-id>
```

## 3. SSH host key

No passphrase. Generate a dedicated host keypair:

```bash
ssh-keygen -t ed25519 -C "dlsite-opds-host" -f dlsite-opds-host -N ""
```

Repository secrets: `SSH_HOST_PRIVATE_KEY`, `SSH_HOST_PUBLIC_KEY`

Cloud-init installs this key for `sshd` at first boot only. To rotate: update
GitHub secrets, delete the droplet, re-run the workflow. The DO deploy SSH key
can stay.

```bash
doctl compute droplet delete <droplet-id> --force
```

Private key secrets must end with a newline after `-----END OPENSSH PRIVATE KEY-----`.
Store keypairs locally — GitHub secrets are write-only.

## 4. DNS and environment variables

Point nightly and prod A records at the same droplet IP (see [Example values](#example-values)).

On **`dev`** — required vars with examples in the table above.

On **`prod`** — same vars, prod domain (e.g. `opds.example.com` /
`https://opds.example.com:2580`).

Cert retry if LE fails after DNS propagation:

```bash
cd /opt/dlsite-opds-caddy && docker compose -f docker-compose.caddy.yml restart
```

DLsite credentials are per-client via HTTP Basic Auth (not server config).

## 5. GitHub variables

**Repository** (not on `dev`/`prod` envs):

| Variable | Default |
| --- | --- |
| `DO_REGION` | `nyc3` |
| `DO_SIZE` | `s-1vcpu-1gb` |
| `DO_IMAGE` | `ubuntu-24-04-x64` |
| `DO_DROPLET_NAME` | `dlsite-opds` |
| `DO_ENABLE_BACKUPS` | `false` |
| `DO_SWAP_SIZE` | `1G` |
| `DO_PROJECT_NAME` | `DLsite OPDS` |
| `DO_PROJECT_PURPOSE` | `Service or API` |
| `DO_PROJECT_ENVIRONMENT` | `Production` |

`DO_SWAP_SIZE`: `0`, `512M`, `1G`, etc. First boot only.

**Per environment** — see [Example values](#example-values) for required `OPDS_DOMAIN` / `DLSITE_OPDS_BASE_URL`.

| Variable | `dev` default | `prod` default |
| --- | --- | --- |
| `APP_PATH` | `/opt/dlsite-opds-nightly` | `/opt/dlsite-opds` |
| `OPDS_CACHE_TTL` | `300` | `300` |
| `OPDS_PAGE_SIZE` | `30` | `30` |
| `OPDS_IMAGE_CACHE_TTL` | `86400` | `86400` |
| `OPDS_PREFETCH_AHEAD` | `5` | `5` |

**Private GHCR package:** repository secret `GHCR_PULL_TOKEN` (PAT with `read:packages`).

## 6. Deploy

Actions → **Provision and Deploy OPDS** → run (or push to `main`).

1. Push `main` — provision droplet, deploy nightly
2. Prod DNS A record → same IP
3. Merge `prod` — deploy prod slot; Caddy picks up both domains

Each deploy:

1. Builds and pushes the container image (after gate approval)
2. Writes `.env` (including `OPDS_EXTERNAL_PORT`: **2580** for prod, **2581** for nightly)
3. Syncs Caddy config and the reload script, opens firewall ports, deploys the app
4. Verifies `https://$OPDS_DOMAIN:$OPDS_EXTERNAL_PORT/healthz`

```text
https://opds-nightly.example.com:2581/opds    # nightly
https://opds.example.com:2580/opds            # prod
curl https://opds.example.com:2580/healthz
```

## Prod promotion

1. Create `prod-gate` (reviewers) and `prod` envs
2. On `prod`: set `OPDS_DOMAIN` / `DLSITE_OPDS_BASE_URL` (e.g. `opds.example.com`, `https://opds.example.com:2580`)
3. Branch `prod` from `main`; protect `prod` (PR required; optional: require `dev` deployment)

Flow: PR → `main` → nightly deploy → PR `main → prod` → prod deploy.

Nightly and prod: separate `data/`, image tags, domains; same droplet.

## SSH

```bash
ssh -i path/to/dlsite-opds-deploy deploy@203.0.113.10
```

```text
/opt/dlsite-opds-nightly/   nightly
/opt/dlsite-opds/           prod
/opt/dlsite-opds-caddy/     Caddy
```

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| OPDS unreachable on `:2580`/`:2581` | Confirm DNS points at the droplet; re-run **Provision and Deploy OPDS** or `sudo /usr/local/bin/reload-dlsite-opds-caddy`; check the DO firewall allows the port |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | [SSH host key](#3-ssh-host-key) |
| `Permission denied (publickey)` | [Deploy SSH key](#2-deploy-ssh-key) |
| `DigitalOcean SSH key dlsite-opds-deploy exists with a different fingerprint` | [Deploy SSH key](#2-deploy-ssh-key) |

Re-deploying updates app containers, syncs the Caddy reload script, refreshes
the firewall rules, and verifies `https://$OPDS_DOMAIN:$OPDS_EXTERNAL_PORT/healthz`.

Provision is skipped when the droplet already exists; cloud-init does not
re-run. Changing GitHub secrets alone does not update a live droplet — deploy
or SSH in.

Pin the host key when testing locally:

```bash
printf '%s %s\n' 203.0.113.10 "$(awk '{print $1" "$2}' dlsite-opds-host.pub)" > known_hosts
ssh -i dlsite-opds-deploy -o UserKnownHostsFile=known_hosts deploy@203.0.113.10 "echo ok"
```

## Teardown

Back up data before deleting the droplet (requires working deploy SSH):

```bash
scp -r deploy@203.0.113.10:/opt/dlsite-opds-nightly/data ./nightly-data.backup
scp -r deploy@203.0.113.10:/opt/dlsite-opds/data ./prod-data.backup
```

Remove droplet `dlsite-opds`, firewall `dlsite-opds-ssh`, SSH key
`dlsite-opds-deploy`, tag `dlsite-opds`. Project is left in place.

```bash
doctl compute droplet list --format ID,Name,PublicIPv4
doctl compute ssh-key list --format ID,Name,FingerPrint
doctl compute droplet delete <droplet-id> --force
doctl compute ssh-key delete <ssh-key-id>
```

## Firewall

Each deploy runs `scripts/ensure-firewall.sh`, which opens TCP **22**, **80**,
**443**, **2580**, and **2581** on the droplet firewall (`dlsite-opds-ssh`).

## Restart

**Restart OPDS** (workflow dispatch, gate approval) restarts one app slot and
reloads Caddy from the current `.env` files.

Reload Caddy manually:

```bash
sudo /usr/local/bin/reload-dlsite-opds-caddy
```

The reload script lives at `infra/digitalocean/reload-dlsite-opds-caddy.sh` in
the repo and is synced to `/opt/dlsite-opds-caddy/reload-dlsite-opds-caddy.sh`
on each deploy. `/usr/local/bin/reload-dlsite-opds-caddy` is a wrapper that
execs it (passwordless sudo for the deploy user).
