# Infrastructure Setup

GitHub Actions provisions a DigitalOcean VPS with the official `doctl` CLI and
deploys dlsite-opds behind Caddy with automatic HTTPS. Pushes to `main` deploy
to a **dev** droplet; promotion to prod is a deliberate `main → prod` PR merge.
No local Terraform or server setup is required.

Each environment is split into a **gate** env (required reviewers, no secrets;
attached to the `gate` job) and a **secrets** env (no reviewers; attached to
`discover`, `provision`, and `deploy`). Approval is requested once per run.

Shared infra secrets (DO token, SSH keys) live at the **repository** level and
fall through from any environment. Per-environment vars (`OPDS_DOMAIN`,
`DLSITE_OPDS_BASE_URL`) live on the `dev` / `prod` environments.

Create before first deploy:

- `dev-gate` — required reviewers, no secrets/vars.
- `dev` — no reviewers; holds `OPDS_DOMAIN`, `DLSITE_OPDS_BASE_URL`, and any
  per-env vars from [Optional GitHub Variables](#5-optional-github-variables).

A `prod` environment and `prod` branch are required for prod deploys — see
[Setting up the prod promotion path](#setting-up-the-prod-promotion-path).

## 1. Create DigitalOcean Token

Create a DigitalOcean API token with these custom scopes:

- `droplet:read`, `droplet:create`
- `ssh_key:read`, `ssh_key:create`
- `firewall:read`, `firewall:create`, `firewall:update`
- `tag:read`, `tag:create`
- `project:read`, `project:create`, `project:update`

Save it as this GitHub repository secret:

```text
DIGITALOCEAN_TOKEN
```

## 2. Create Deploy SSH Key

Create a key pair:

```bash
ssh-keygen -t ed25519 -C "dlsite-opds-deploy" -f dlsite-opds-deploy
```

Save the private key as this GitHub repository secret:

```text
SSH_DEPLOY_PRIVATE_KEY
```

Save the public key as this GitHub repository secret:

```text
SSH_DEPLOY_PUBLIC_KEY
```

## 3. Create SSH Host Key

Create a key pair for the server's SSH host identity (prevents MITM during deploy):

```bash
ssh-keygen -t ed25519 -C "dlsite-opds-host" -f dlsite-opds-host
```

Save the private key as this GitHub repository secret:

```text
SSH_HOST_PRIVATE_KEY
```

Save the public key as this GitHub repository secret:

```text
SSH_HOST_PUBLIC_KEY
```

**Note:** both private key secrets must end with a trailing newline after
`-----END OPENSSH PRIVATE KEY-----`. Without it, `sshd` fails to load the host
key and deploys fail host-key verification.

**Store both keypairs in a password manager.** GitHub secrets are write-only —
lost local copies are unrecoverable. See [Connect to the Droplet](#connect-to-the-droplet)
for the rotation path.

## 4. Configure DNS and Environment Variables

Before the first deploy, create an **A record** for your dev domain pointing to
the droplet IP (available after the first provision run completes).

On the **`dev` environment**, set these variables:

| Variable | Example |
| --- | --- |
| `OPDS_DOMAIN` | `opds-dev.example.com` |
| `DLSITE_OPDS_BASE_URL` | `https://opds-dev.example.com` |

Caddy obtains a Let's Encrypt certificate automatically when DNS resolves to
the droplet. If cert issuance fails on first boot, wait for DNS propagation and
run `docker compose restart caddy` on the droplet.

DLsite credentials are **not** configured server-side. Each OPDS reader provides
its own username and password via HTTP Basic Auth.

## 5. Optional GitHub Variables

GitHub variables (not secrets). Set at repo level for shared values, or on a
specific environment (`prod`, `dev`) to override. Unset → falls back to
the default below.

| Variable | Default |
| --- | --- |
| `DO_REGION` | `nyc3` |
| `DO_SIZE` | `s-1vcpu-1gb` |
| `DO_IMAGE` | `ubuntu-24-04-x64` |
| `DO_DROPLET_NAME` | `dlsite-opds` |
| `DO_ENABLE_BACKUPS` | `false` |
| `DO_SWAP_SIZE` | `1G` |
| `APP_PATH` | `/opt/dlsite-opds` |
| `DO_PROJECT_NAME` | `DLsite OPDS` |
| `DO_PROJECT_PURPOSE` | `Service or API` |
| `DO_PROJECT_ENVIRONMENT` | `Production` |
| `OPDS_CACHE_TTL` | `300` |
| `OPDS_PAGE_SIZE` | `30` |
| `OPDS_IMAGE_CACHE_TTL` | `86400` |
| `OPDS_PREFETCH_AHEAD` | `5` |

`DO_SWAP_SIZE` accepts a positive integer optionally suffixed `K`/`M`/`G`, or `0` to disable.
Applied only at first boot via cloud-init — changing it doesn't affect existing droplets.

Set `DO_ENABLE_BACKUPS` to `true` before the first deploy if you want DigitalOcean
Droplet backups. Backups add 20% to the droplet cost. You can also back up the
data directory manually via `scp` — see [Backup Before Deleting](#backup-before-deleting).

### Optional: private GHCR package

If the container image is not publicly readable, add a repository secret:

```text
GHCR_PULL_TOKEN
```

Use a GitHub personal access token with `read:packages` scope. The deploy
workflow logs the droplet into GHCR before `docker compose pull`.

## 6. Run Deploy

In GitHub:

1. Open `Actions`.
2. Select `Provision and Deploy OPDS`.
3. Run the workflow (or push to `main`).

The workflow builds and pushes the container image, creates or reuses the VPS,
writes `.env`, syncs deploy artifacts, and runs Docker Compose.

Future pushes to `main` deploy to dev automatically; each run pauses at
`gate` for `dev-gate` reviewer approval before `discover`, `provision`, and
`deploy` proceed. Prod is reached only by merging `main → prod` — see
[Setting up the prod promotion path](#setting-up-the-prod-promotion-path).

After deploy, connect your OPDS reader to:

```text
https://<OPDS_DOMAIN>/opds
```

Verify health: `curl https://<OPDS_DOMAIN>/healthz`

## Setting up the prod promotion path

Required for prod deploys. Without this, the workflow only ever targets dev.

1. Create two GitHub environments:
   - `prod-gate` — required reviewers, no secrets/vars.
   - `prod` — no reviewers.
2. On `prod`, add `OPDS_DOMAIN` and `DLSITE_OPDS_BASE_URL` for your prod domain.
3. On `prod`, add these vars (shared infra secrets stay at repo level; the dev
   environment carries the dev-droplet overrides):

   | Variable | Value |
   | --- | --- |
   | `DO_DROPLET_NAME` | `dlsite-opds` |
   | `APP_PATH` | `/opt/dlsite-opds` |
   | `DO_PROJECT_NAME` | `DLsite OPDS` |
   | `DO_PROJECT_ENVIRONMENT` | `Production` |
   | `DO_SIZE` | `s-1vcpu-1gb` |

   The dev environment should mirror the inverse:

   | Variable | Value |
   | --- | --- |
   | `DO_DROPLET_NAME` | `dlsite-opds-dev` |
   | `APP_PATH` | `/opt/dlsite-opds-dev` |
   | `DO_PROJECT_NAME` | `DLsite OPDS` |
   | `DO_PROJECT_ENVIRONMENT` | `Development` |
   | `DO_SIZE` | `s-1vcpu-1gb` |

4. Create the `prod` branch from `main` and push it. Pushes and merges to
   `prod` deploy to the prod droplet, gated by `prod-gate`.
5. Add branch protection on `prod`:
   - Require a pull request before merging.
   - Require deployments to succeed before merging → add `dev`. This forces
     the head SHA to have already passed a dev deploy before it can land on
     prod.
   - (Optional) restrict who can merge, require approvals, dismiss stale
     approvals on push.

**Promotion workflow:** feature branch → PR to `main` → merge → dev deploys
automatically (gated by `dev-gate`) → open PR `main → prod` → branch
protection confirms the head SHA succeeded on dev → merge → prod deploys
(gated by `prod-gate`).

Prod and dev share no state: separate droplets, separate `data/` directories,
separate domains.

## Connect to the Droplet

Get the droplet IPv4 from the latest workflow's `discover` job, `doctl compute
droplet list`, or the DO console, then connect with the deploy private key from
Section 2:

```bash
ssh -i path/to/dlsite-opds-deploy deploy@<droplet-ip>
```

Accept the host-key prompt on first connection.

**Lost the deploy key:** regenerate per Section 2, replace the
`SSH_DEPLOY_PRIVATE_KEY` / `SSH_DEPLOY_PUBLIC_KEY` secrets, back up the data
directory (see [Backup Before Deleting](#backup-before-deleting)), delete the
droplet, and re-run the workflow.

## Backup Before Deleting

The dev data directory is:

```text
/opt/dlsite-opds-dev/data/
```

Download it before deleting the Droplet:

```bash
scp -r deploy@your_server_ip:/opt/dlsite-opds-dev/data ./data.backup
```

To remove the dev deployment, delete these DigitalOcean resources:

```text
Droplet:  dlsite-opds-dev
Firewall: dlsite-opds-dev-ssh
SSH key:  dlsite-opds-dev-deploy
Tag:      dlsite-opds-dev
```

If the prod promotion path is configured, prod uses the same resource names
without the `-dev` suffix:

```text
Data:     /opt/dlsite-opds/data/
Droplet:  dlsite-opds
Firewall: dlsite-opds-ssh
SSH key:  dlsite-opds-deploy
Tag:      dlsite-opds
```

## Firewall updates on existing droplets

The provision job is skipped when a droplet already exists. Firewall rule changes
in `scripts/provision-digitalocean.sh` (for example opening ports 80/443) will
not apply to a living droplet. To force re-provision, delete the droplet via the
DO console or `doctl compute droplet delete`, then re-run the workflow. You can
also update the firewall manually in the DigitalOcean console.

## Manual restart

Use the `Restart OPDS` workflow in GitHub Actions to restart containers without
a full deploy. Requires approval via `dev-gate` or `prod-gate`.
