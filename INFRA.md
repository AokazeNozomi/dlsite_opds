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

### `prod` environment

| Variable | Example |
| --- | --- |
| `OPDS_DOMAIN` | `opds.example.com` |
| `DLSITE_OPDS_BASE_URL` | `https://opds.example.com:2580` |
| `APP_PATH` | `/opt/dlsite-opds` |

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

No passphrase — cloud-init writes this key for `sshd` at first boot with no prompt.

```bash
ssh-keygen -t ed25519 -C "dlsite-opds-host" -f dlsite-opds-host -N ""
```

Repository secrets: `SSH_HOST_PRIVATE_KEY`, `SSH_HOST_PUBLIC_KEY`

Cloud-init writes this key for `sshd` at first boot only. If you rotate it:
update GitHub secrets, delete the droplet only, re-run workflow. The DO deploy
SSH key can stay.

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

Each `main`/`prod` run requires gate approval before the image is built and
deployed.

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

| Symptom | Cause | Fix |
| --- | --- | --- |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | Host key secret ≠ droplet `sshd` key | [SSH host key](#3-ssh-host-key) |
| `Permission denied (publickey)` | Deploy key secret ≠ droplet `authorized_keys` | [Deploy SSH key](#2-deploy-ssh-key) |
| `DigitalOcean SSH key dlsite-opds-deploy exists with a different fingerprint` | GitHub public key ≠ DO SSH key | [Deploy SSH key](#2-deploy-ssh-key) |

Provision is **skipped** when the droplet already exists. Cloud-init does not
re-run; changing GitHub secrets alone does not update a live droplet.

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

## Firewall on existing droplets

Provision is skipped if the droplet exists; firewall script changes won't apply.
Delete droplet and re-run, or update firewall in DO console.

## Restart

**Restart OPDS** workflow (gate approval) restarts one app slot only.

Reload Caddy manually:

```bash
sudo /usr/local/bin/reload-dlsite-opds-caddy
```
